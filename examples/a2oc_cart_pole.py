import os
import rainy
from rainy.utils.cli import run_cli
from rainy.envs import MultiProcEnv
from torch import optim


def config() -> rainy.Config:
    c = rainy.Config()
    c.max_steps = int(1e3)
    c.nworkers = 12
    c.nsteps = 5
    c.set_parallel_env(MultiProcEnv)
    c.set_optimizer(lambda params: optim.RMSprop(params, lr=0.001))
    c.grad_clip = 0.5
    c.eval_freq = None
    c.entropy_weight = 0.001
    c.value_loss_weight = 1.0
    c.set_net_fn('option-critic', rainy.net.option_critic.fc_shared(num_options=2))
    return c


if __name__ == '__main__':
    run_cli(config(), rainy.agents.A2ocAgent, script_path=os.path.realpath(__file__))
