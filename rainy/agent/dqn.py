import numpy as np
from numpy import ndarray
import torch
from torch import nn
from typing import Tuple
from .base import OneStepAgent
from ..config import Config
from ..envs import Action, State


class DqnAgent(OneStepAgent):
    def __init__(self, config: Config) -> None:
        super().__init__(config)
        self.net = config.net('value')
        self.target_net = config.net('value')
        self.optimizer = config.optimizer(self.net.parameters())
        self.criterion = nn.MSELoss()
        self.policy = config.explorer(self.net)
        self.replay = config.replay_buffer()
        self.batch_indices = torch.arange(
            config.batch_size,
            device=self.config.device(),
            dtype=torch.long
        )

    def members_to_save(self) -> Tuple[str, ...]:
        return "net", "target_net", "policy", "total_steps"

    def best_action(self, state: State) -> Action:
        action_values = self.net.action_values(state).detach()
        # Here supposes action_values is 1×(action_dim) array
        return action_values.argmax()

    def step(self, state: State) -> Tuple[State, float, bool]:
        train_started = self.total_steps > self.config.train_start
        if train_started:
            action = self.policy.select_action(self.env.state_to_array(state))
        else:
            action = self.random_action()
        next_state, reward, done, _ = self.env.step(action)
        self.replay.append(state, action, reward, next_state, done)
        if train_started:
            self._train()
        return next_state, reward, done

    def _train(self) -> None:
        observations = self.replay.sample_with_state_wrapper(
            self.config.batch_size,
            self.env.state_to_array
        )
        states, actions, rewards, next_states, is_terms = map(np.asarray, zip(*observations))
        q_next = self.target_net(next_states).detach()
        if self.config.double_q:
            # Here supposes action_values is batch_size×(action_dim) array
            action_values = self.net.action_values(next_states, nostack=True).detach()
            q_next = q_next[self.batch_indices, action_values.argmax(dim=-1)]
        else:
            q_next, _ = q_next.max(1)
        q_next *= self.config.device.tensor(1.0 - is_terms) * self.config.discount_factor
        q_next += self.config.device.tensor(rewards)
        q_current = self.net(states)[self.batch_indices, actions]
        loss = self.criterion(q_current, q_next)
        self.optimizer.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(self.net.parameters(), self.config.grad_clip)
        self.optimizer.step()
        if self.total_steps % self.config.step_log_freq == 0:
            self.logger.exp('loss', {
                'total_steps': self.total_steps,
                'loss': loss.item(),
            })
        if self.total_steps % self.config.sync_freq == 0:
            self.sync_target_net()

    def sync_target_net(self) -> None:
        self.target_net.load_state_dict(self.net.state_dict())
