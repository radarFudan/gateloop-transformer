# taken from S5-pytorch repository
# https://github.com/i404788/s5-pytorch/blob/74e2fdae00b915a62c914bf3615c0b8a4279eb84/s5/jax_compat.py#L51-L134

# will be adapted to test out GateLoop on a small scale https://arxiv.org/abs/2311.01927

# todo: forget about generalized function and just make it all specific for axis = 1 (usually sequence dimension for transformer tokens)

import torch
from torch import Tensor
import torch.nn.functional as F
from functools import partial

from typing import Tuple, Callable

def safe_map(f, *args):
    args = list(map(list, args))
    n = len(args[0])
    for arg in args[1:]:
        assert len(arg) == n, f'length mismatch: {list(map(len, args))}'
    return list(map(f, *args))

def slice_along_axis(start, end, stride=None, axis=0):
    return (slice(None),) * axis + (slice(start, end, stride),)

# Pytorch impl. of jax.lax.associative_scan

def associative_scan(
    operator: Callable,
    elems: Tuple[Tensor, Tensor],
    axis = 0
):
    num_elems = int(elems[0].shape[axis])

    if not all(int(elem.shape[axis]) == num_elems for elem in elems[1:]):
        raise ValueError('Array inputs to associative_scan must have the same '
                         'first dimension. (saw: {})'
                         .format([elem.shape for elem in elems]))

    def _scan(elems):
        """Perform scan on `elems`."""
        num_elems = elems[0].shape[axis]

        if num_elems < 2:
            return elems

        # Combine adjacent pairs of elements.

        reduced_elems = operator(
          [elem[slice_along_axis(0, -1, stride=2, axis=axis)] for elem in elems],
          [elem[slice_along_axis(1, None, stride=2, axis=axis)] for elem in elems])

        # Recursively compute scan for partially reduced tensors.

        odd_elems = _scan(reduced_elems)

        if num_elems % 2 == 0:
            even_elems = operator(
                [e[slice_along_axis(0, -1, axis=axis)] for e in odd_elems],
                [e[slice_along_axis(2, None, stride=2, axis=axis)] for e in elems])
        else:
            even_elems = operator(
                odd_elems,
                [e[slice_along_axis(2, None, stride=2, axis=axis)] for e in elems])

        # The first element of a scan is the same as the first element
        # of the original `elems`.

        even_elems = [
          torch.cat([elem[slice_along_axis(0, 1, axis=axis)], result], dim=axis)
          if result.shape.numel() > 0 and elem.shape[axis] > 0 else
          result if result.shape.numel() > 0 else
          elem[slice_along_axis(0, 1, axis=axis)]  # Jax allows/ignores concat with 0-dim, Pytorch does not
          for (elem, result) in zip(elems, even_elems)]

        return list(safe_map(partial(_interleave, axis=axis), even_elems, odd_elems))

    scans = _scan(elems)

    return scans

def _interleave(a, b, axis):
    # https://stackoverflow.com/questions/60869537/how-can-i-interleave-5-pytorch-tensors
    if b_trunc := (a.shape[axis] == b.shape[axis] + 1):
        pad = [0, 0] * b.ndim
        pad[(b.ndim-axis-1)*2+1] = 1 # +1=always end of dim, pad-order is reversed so start is at end
        b = F.pad(b, pad)

    stacked = torch.stack([a, b], dim=axis+1)
    interleaved = torch.flatten(stacked, start_dim=axis, end_dim=axis+1)

    if not b_trunc:
        return interleaved

    # TODO: find torch alternative for slice_along axis for torch.jit.script to work
    return interleaved[slice_along_axis(0, b.shape[axis]+a.shape[axis]-1, axis=axis)]