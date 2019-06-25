from copy import deepcopy
import torch
from torch import Tensor
from torch.distributions import Categorical
from typing import Optional, Tuple
from .base import NStepParallelAgent
from ..config import Config
from ..lib.rollout import RolloutStorage
from ..net import OptionCriticNet
from ..prelude import Action, Array, State
from ..utils.misc import has_freq_in_interval


class A2ocRolloutStorage(RolloutStorage[State]):
    def __init__(self, raw: RolloutStorage, num_options: int) -> None:
        self.__dict__.update(raw.__dict__)
        init_option = self.device.zeros(self.nworkers, dtype=torch.long)
        self.option_list: List[Tensor] = [init_option]
        self.beta_list: List[Tensor] = []
        self.eps_list: List[float] = []
        self.beta_adv = torch.zeros_like(self.batch_values)
        self.noptions = num_options

    def reset(self) -> None:
        super().reset()
        self.option_list = [self.option_list[-1]]
        self.beta_list.clear()
        self.eps_list.clear()

    def push_options(
            self,
            option: Tensor,
            beta: Tensor,
            epsilon: float,
    ) -> None:
        self.option_list.append(option)
        self.beta_list.append(beta)
        self.eps_list.append(epsilon)

    def batch_options(self) -> Tuple[Tensor, Tensor]:
        batch_opt = torch.cat(self.option_list, dim=0)
        return batch_opt[:-self.nworkers].unsqueeze(-1), batch_opt[self.nworkers:].unsqueeze(-1)

    def calc_returns(self, next_value: Tensor, gamma: float, xi: float) -> None:
        self.returns[-1] = next_value
        rewards = self.device.tensor(self.rewards)
        for i in reversed(range(self.nsteps)):
            self.returns[i] = self.returns[i + 1] * gamma * self.masks[i + 1] + rewards[i]
            opt, prev_opt = self.option_list[i + 1], self.option_list[i]
            opt_q, eps = self.values[i], self.eps_list[i]
            self.advs[i] = self.returns[i] - opt_q.gather(1, opt.unsqueeze(-1)).squeeze_(-1)
            v = (1 - eps) * opt_q.max(dim=-1)[0] + eps * opt_q.mean(dim=-1)
            q = opt_q.gather(1, prev_opt.unsqueeze(-1)).squeeze_(-1)
            self.beta_adv[i] = q - v + xi


@torch._jit_internal.weak_script
def _sample(prob: Tensor) -> Tensor:
    return torch.multinomial(prob, 1, True).flatten()


@torch.jit.script
def _sample_option(
        opt_q: Tensor,
        beta: Tensor,
        epsilon: float,
        prev_option: Tensor,
        is_initial_states: Tensor
) -> Tensor:
    noptions = float(opt_q.size(1))
    max_prob = 1.0 - epsilon + epsilon / noptions
    eps_prob = torch.zeros_like(opt_q).add_(epsilon / noptions)
    epsgreedy_prob = eps_prob.scatter_(1, opt_q.argmax(dim=1, keepdim=True), max_prob)

    mask = torch.zeros_like(opt_q)
    mask[:, prev_option].fill_(1.0)

    return torch.where(
        is_initial_states,
        _sample(epsgreedy_prob),
        _sample((1 - beta) * mask + beta * epsgreedy_prob)
    )


class A2ocAgent(NStepParallelAgent[State]):
    """A2OC: Advantage Actor Option Critic
    """
    def __init__(self, config: Config) -> None:
        super().__init__(config)
        self.net: OptionCriticNet = config.net('option-critic')
        self.noptions = self.net.num_options
        self.target_net = deepcopy(self.net)
        self.optimizer = config.optimizer(self.net.parameters())
        self.worker_indices = torch.arange(
            config.nworkers,
            device=self.config.device.unwrapped,
            dtype=torch.long
        )
        self.batch_indices = torch.arange(
            config.nworkers * config.nsteps,
            device=self.config.device.unwrapped,
            dtype=torch.long
        )
        self.storage = A2ocRolloutStorage(self.storage, self.noptions)
        self.opt_epsilon_cooler = config.opt_epsilon_cooler()
        self.is_initial_states = config.device.ones(config.nworkers, dtype=torch.uint8)

    def members_to_save(self) -> Tuple[str, ...]:
        return ("net",)

    def eval_action(self, state: Array) -> Action:
        if len(state.shape) == len(self.net.state_dim):
            # treat as batch_size == 1
            state = np.stack([state])
        with torch.no_grad():
            opt_policy, opt_q, beta = self.net(states)
            options = _sample_option(
                opt_q,
                beta,
                self.config.opt_epsilon_eval,
                self.prev_options,
                self.is_initial_states
            )
            policy = opt_policy[self.worker_indices, options]
        if self.config.eval_deterministic:
            return policy.best_action().squeeze().cpu().numpy()
        else:
            return policy.action().squeeze().cpu().numpy()

    def eval_action_parallel(
            self,
            states: Array,
            mask: torch.Tensor,
            ent: Optional[Array[float]] = None
    ) -> Array[Action]:
        pass

    @property
    def prev_options(self) -> Tensor:
        return self.storage.option_list[-1]

    @torch.no_grad()
    def _one_step(self, states: Array[State]) -> Array[State]:
        opt_policy, opt_q, beta = self.net(self.penv.extract(states))
        eps = self.opt_epsilon_cooler()
        options = _sample_option(opt_q, beta, eps, self.prev_options, self.is_initial_states)
        policy = opt_policy[self.worker_indices, options]
        next_states, rewards, done, info = self.penv.step(policy.action().squeeze().cpu().numpy())
        self.episode_length += 1
        self.rewards += rewards
        self.report_reward(done, info)
        self.storage.push(next_states, rewards, done, policy=policy, value=opt_q)
        self.storage.push_options(options, beta, eps)
        self.is_initial_states = self.config.device.tensor(done).byte()
        return next_states

    @torch.no_grad()
    def _next_value(self, states: Array[State]) -> Tensor:
        opt_q, beta = self.net.q_and_beta(self.penv.extract(states))
        idx = self.worker_indices, self.prev_options
        beta = beta[idx]
        return (1 - beta) * opt_q[idx] + beta * opt_q.max(dim=-1)[0]

    def nstep(self, states: Array[State]) -> Array[State]:
        step_before = self.total_steps
        for _ in range(self.config.nsteps):
            states = self._one_step(states)

        # sync target network
        if has_freq_in_interval(step_before, self.batch_indices.size(0), self.config.sync_freq):
            self.target_net.load_state_dict(self.net.state_dict())

        next_value = self._next_value(states)
        self.storage.calc_returns(
            next_value,
            self.config.discount_factor,
            self.config.opt_termination_xi
        )

        prev_options, options = self.storage.batch_options()
        adv = self.storage.advs[:-1].flatten()
        beta_adv = self.storage.beta_adv.flatten()
        ret = self.storage.returns[-1].flatten()
        masks = self.storage.batch_masks()

        opt_policy, opt_q, beta = self.net(self.storage.batch_states(self.penv))
        policy = opt_policy[self.batch_indices, options]
        policy.set_action(self.storage.batch_actions())

        expected_v = opt_q.gather(1, options)
        v_loss = (expected_v - ret).pow(2).mean()
        policy_loss = -(policy.log_prob() * adv).mean()
        beta_loss = beta.gather(1, prev_options).mul(beta_adv).mul(masks).mean()
        entropy = policy.entropy().mean()
        entropy_loss = -self.config.entropy_weight * entropy

        self.optimizer.zero_grad()
        (policy_loss + 0.5 * v_loss + beta_loss + entropy_loss).backward()
        self.optimizer.step()

        self.report_loss(
            policy_loss=policy_loss.item(),
            v_loss=v_loss.item(),
            entropy=entropy.item(),
        )
        self.storage.reset()
        return states
