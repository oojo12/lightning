# Copyright The PyTorch Lightning team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from collections import namedtuple

import pytest
import torch

import tests_pytorch.helpers.pipelines as tpipes
from pytorch_lightning import Trainer
from pytorch_lightning.accelerators import MPSAccelerator
from pytorch_lightning.demos.boring_classes import BoringModel
from pytorch_lightning.utilities.imports import _TORCHTEXT_LEGACY
from tests_pytorch.helpers.imports import Batch, Dataset, Example, Field, LabelField
from tests_pytorch.helpers.runif import RunIf


@RunIf(mps=True)
def test_get_mps_stats():
    current_device = torch.device("mps")
    device_stats = MPSAccelerator().get_device_stats(current_device)
    fields = ["M1_vm_percent", "M1_percent", "M1_swap_percent"]

    for f in fields:
        assert any(f in h for h in device_stats.keys())


@RunIf(mps=True)
def test_mps_availability():
    assert MPSAccelerator.is_available()


@RunIf(mps=True)
def test_warning_if_mps_not_used():
    with pytest.warns(UserWarning, match="MPS available but not used. Set `accelerator` and `devices`"):
        Trainer()


@RunIf(mps=True)
@pytest.mark.parametrize("accelerator_value", ["mps", MPSAccelerator()])
def test_trainer_mps_accelerator(accelerator_value):
    trainer = Trainer(accelerator=accelerator_value)
    assert isinstance(trainer.accelerator, MPSAccelerator)
    assert trainer.num_devices == 1


@RunIf(mps=True)
@pytest.mark.parametrize("devices", [1, [0], "-1"])
def test_single_gpu_model(tmpdir, devices):
    """Make sure single GPU works."""
    trainer_options = dict(
        default_root_dir=tmpdir,
        enable_progress_bar=False,
        max_epochs=1,
        limit_train_batches=0.1,
        limit_val_batches=0.1,
        accelerator="mps",
        devices=devices,
    )

    model = BoringModel()
    tpipes.run_model_test(trainer_options, model)


@RunIf(mps=True)
def test_single_gpu_batch_parse():
    trainer = Trainer(accelerator="mps", devices=1)

    # non-transferrable types
    primitive_objects = [None, {}, [], 1.0, "x", [None, 2], {"x": (1, 2), "y": None}]
    for batch in primitive_objects:
        data = trainer.strategy.batch_to_device(batch, torch.device("mps"))
        assert data == batch

    # batch is just a tensor
    batch = torch.rand(2, 3)
    batch = trainer.strategy.batch_to_device(batch, torch.device("mps"))
    assert batch.device.index == 0 and batch.type() == "torch.mps.FloatTensor"

    # tensor list
    batch = [torch.rand(2, 3), torch.rand(2, 3)]
    batch = trainer.strategy.batch_to_device(batch, torch.device("mps"))
    assert batch[0].device.index == 0 and batch[0].type() == "torch.mps.FloatTensor"
    assert batch[1].device.index == 0 and batch[1].type() == "torch.mps.FloatTensor"

    # tensor list of lists
    batch = [[torch.rand(2, 3), torch.rand(2, 3)]]
    batch = trainer.strategy.batch_to_device(batch, torch.device("mps"))
    assert batch[0][0].device.index == 0 and batch[0][0].type() == "torch.mps.FloatTensor"
    assert batch[0][1].device.index == 0 and batch[0][1].type() == "torch.mps.FloatTensor"

    # tensor dict
    batch = [{"a": torch.rand(2, 3), "b": torch.rand(2, 3)}]
    batch = trainer.strategy.batch_to_device(batch, torch.device("mps"))
    assert batch[0]["a"].device.index == 0 and batch[0]["a"].type() == "torch.mps.FloatTensor"
    assert batch[0]["b"].device.index == 0 and batch[0]["b"].type() == "torch.mps.FloatTensor"

    # tuple of tensor list and list of tensor dict
    batch = ([torch.rand(2, 3) for _ in range(2)], [{"a": torch.rand(2, 3), "b": torch.rand(2, 3)} for _ in range(2)])
    batch = trainer.strategy.batch_to_device(batch, torch.device("mps"))
    assert batch[0][0].device.index == 0 and batch[0][0].type() == "torch.mps.FloatTensor"

    assert batch[1][0]["a"].device.index == 0
    assert batch[1][0]["a"].type() == "torch.mps.FloatTensor"

    assert batch[1][0]["b"].device.index == 0
    assert batch[1][0]["b"].type() == "torch.mps.FloatTensor"

    # namedtuple of tensor
    BatchType = namedtuple("BatchType", ["a", "b"])
    batch = [BatchType(a=torch.rand(2, 3), b=torch.rand(2, 3)) for _ in range(2)]
    batch = trainer.strategy.batch_to_device(batch, torch.device("mps"))
    assert batch[0].a.device.index == 0
    assert batch[0].a.type() == "torch.mps.FloatTensor"

    # non-Tensor that has `.to()` defined
    class CustomBatchType:
        def __init__(self):
            self.a = torch.rand(2, 2)

        def to(self, *args, **kwargs):
            self.a = self.a.to(*args, **kwargs)
            return self

    batch = trainer.strategy.batch_to_device(CustomBatchType(), torch.device("mps"))
    assert batch.a.type() == "torch.mps.FloatTensor"

    # torchtext.data.Batch
    if not _TORCHTEXT_LEGACY:
        return

    samples = [
        {"text": "PyTorch Lightning is awesome!", "label": 0},
        {"text": "Please make it work with torchtext", "label": 1},
    ]

    text_field = Field()
    label_field = LabelField()
    fields = {"text": ("text", text_field), "label": ("label", label_field)}

    examples = [Example.fromdict(sample, fields) for sample in samples]
    dataset = Dataset(examples=examples, fields=fields.values())
    # Batch runs field.process() that numericalizes tokens, but it requires to build dictionary first
    text_field.build_vocab(dataset)
    label_field.build_vocab(dataset)

    batch = Batch(data=examples, dataset=dataset)

    with pytest.deprecated_call(match="The `torchtext.legacy.Batch` object is deprecated"):
        batch = trainer.strategy.batch_to_device(batch, torch.device("mps"))

    assert batch.text.type() == "torch.mps.LongTensor"
    assert batch.label.type() == "torch.mps.LongTensor"
