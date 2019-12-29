import gym
from gym import spaces
import numpy as np
from typing import Any, Generic, Sequence, Optional, Tuple
from ..prelude import Action, Array, State


class EnvSpec:
    def __init__(
        self,
        state_dim: Sequence[int],
        action_space: gym.Space,
        use_reward_monitor: bool = False,
    ) -> None:
        """Properties which are common both in EnvExt and ParallelEnv
        """
        self.state_dim = state_dim
        self.action_space = action_space
        self.use_reward_monitor = use_reward_monitor
        if isinstance(action_space, spaces.Discrete):
            self.action_dim = action_space.n
            self._act_range = 0, action_space.n
        elif isinstance(action_space, spaces.Box):
            if len(action_space.shape) != 1:
                raise RuntimeError("Box space with shape >= 2 is not supportd")
            self.action_dim = action_space.shape[0]
            self._act_range = action_space.low, action_space.high
        else:
            raise RuntimeError("{} is not supported".format(type(action_space)))

    def clip_action(self, act: Array) -> Array:
        return np.clip(act, *self._act_range)

    def random_action(self) -> Action:
        return self.action_space.sample()

    def random_actions(self, n: int) -> Array[Action]:
        return np.array([self.action_space.sample() for _ in range(n)])

    def is_discrete(self) -> bool:
        return isinstance(self.action_space, spaces.Discrete)

    def __repr__(self) -> str:
        return "EnvSpec(state_dim: {} action_space: {})".format(
            self.state_dim, self.action_space
        )


class EnvExt(gym.Env, Generic[Action, State]):
    def __init__(self, env: gym.Env, obs_shape: Optional[spaces.Space] = None) -> None:
        self._env = env
        if obs_shape is None:
            obs_shape = env.observation_space.shape
            if obs_shape is None:
                raise NotImplementedError(
                    f"Failed detect state dimension from {env.obs_shape}!"
                )
        self.spec = EnvSpec(obs_shape, self._env.action_space)

    def close(self):
        """
        Inherited from gym.Env.
        """
        self._env.close

    def reset(self) -> State:
        """
        Inherited from gym.Env.
        """
        return self._env.reset()

    def render(self, mode: str = "human") -> None:
        """
        Inherited from gym.Env.
        """
        self._env.render(mode=mode)

    def seed(self, seed: int) -> None:
        """
        Inherited from gym.Env.
        """
        self._env.seed(seed)

    def step(self, action: Action) -> Tuple[State, float, bool, Any]:
        """
        Inherited from gym.Env.
        """
        return self._env.step(action)

    def step_and_render(
        self, action: Action, render: bool = False
    ) -> Tuple[State, float, bool, Any]:
        res = self._env.step(action)
        if render:
            self.render()
        return res

    def step_and_reset(self, action: Action) -> Tuple[State, float, bool, Any]:
        state, reward, done, info = self.step(action)
        if done:
            state = self.reset()
        return state, reward, done, info

    @property
    def unwrapped(self) -> gym.Env:
        """
        Inherited from gym.Env.
        """
        return self._env.unwrapped

    @property
    def action_dim(self) -> int:
        """
        Extended method.
        Returns a ndim of action space.
        """
        return self.spec.action_dim

    @property
    def state_dim(self) -> Sequence[int]:
        """
        Extended method.
        Returns a shape of observation space.
        """
        return self.spec.state_dim

    @property
    def use_reward_monitor(self) -> bool:
        """Atari wrappers need RewardMonitor for evaluation.
        """
        return self.spec.use_reward_monitor

    @property
    def observation_space(self) -> gym.Space:
        return self._env.observation_space

    @property
    def action_space(self) -> gym.Space:
        return self._env.action_space

    def extract(self, state: State) -> Array:
        """
        Extended method.
        Convert state to ndarray.
        It's useful for the cases where numpy.ndarray representation is too large to
        throw it to replay buffer directly.
        """
        return state  # type: ignore

    def save_history(self, file_name: str) -> None:
        """
        Extended method.
        Save agent's action history to file.
        """
        import warnings

        warnings.warn("This environment does not support save_history!")

    def __repr__(self) -> str:
        return "EnvExt({})".format(self._env)
