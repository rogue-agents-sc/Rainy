from numpy import ndarray
import torch
from torch import nn, Tensor
from .body import NetworkBody
from .head import NetworkHead
from ..lib import Device


class ValueNet(nn.Module):
    """State -> [Value..]
    """

    def __init__(
            self,
            body: NetworkBody,
            head: NetworkHead,
            device: Device = Device(),
    ) -> None:
        assert body.output_dim == head.input__dim, \
            'body output and head input must have a same dimention'
        self.head = head
        self.body = body
        self.device = device

    @property
    def state_dim(self) -> int:
        self.body.input_dim

    @property
    def action_dim(self) -> int:
        self.head.output_dim

    def action_values(self, state: ndarray) -> Tensor:
        x = self.device.tensor(state)
        x = self.body(x)
        x = self.head(x)
        return x


