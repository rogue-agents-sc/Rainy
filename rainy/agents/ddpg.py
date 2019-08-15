from copy import deepcopy
import numpy as np
import torch
from torch import nn, Tensor
from typing import Tuple
from .base import OneStepAgent
from ..config import Config
from ..prelude import Action, Array, State


class DdpgAgent(OneStepAgent):
    def __init__(self, config: Config) -> None:
        super().__init__(config)
        self.net = config.net('ddpg')
        self.target_net = deepcopy(self.net)
        self.actor_optim = config.optimizer(self.net.actor.parameters(), key='actor')
        self.critic_optim = config.optimizer(self.net.actor.parameters(), key='critic')
        self.explorer = config.explorer()
        self.replay = config.replay_buffer()
        self.batch_indices = config.device.indices(config.replay_batch_size)

    def set_mode(self, train: bool = True) -> None:
        self.net.train(mode=train)

    def members_to_save(self) -> Tuple[str, ...]:
        return "net", "target_net", "policy", "total_steps"

    @torch.no_grad()
    def eval_action(self, state: Array) -> Action:
        return self.eval_policy.select_action(state, self.net).item()  # type: ignore

    def step(self, state: State) -> Tuple[State, float, bool, dict]:
        train_started = self.total_steps > self.config.train_start
        if train_started:
            action = self.explorer.add_noise(self.net.action(state))
        else:
            action = self.env.spec.random_action()
        action = self.env.spec.clip_action(action)
        next_state, reward, done, info = self.env.step(action)
        self.replay.append(state, action, reward, next_state, done)
        if train_started:
            self._train()
        return next_state, reward, done, info

    def _q_next(self, next_states: Array) -> Tensor:
        return self.target_net(next_states).max(1)[0]

    def _train(self) -> None:
        obs = self.replay.sample(self.config.replay_batch_size)
        obs = [ob.to_ndarray(self.env.extract) for ob in obs]
        states, actions, rewards, next_states, done = map(np.asarray, zip(*obs))
        with torch.no_grad():
            q_next = self._q_next(next_states)
        q_next *= self.config.device.tensor(1.0 - done) * self.config.discount_factor
        q_next += self.config.device.tensor(rewards)
        q_current = self.net(states)[self.batch_indices, actions]
        loss = self.criterion(q_current, q_next)
        self.optimizer.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(self.net.parameters(), self.config.grad_clip)
        self.optimizer.step()
        self.report_loss(value_loss=loss.item())
        if self.total_steps % self.config.sync_freq == 0:
            self.target_net.load_state_dict(self.net.state_dict())
