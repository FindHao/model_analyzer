# Copyright (c) 2021, NVIDIA CORPORATION. All rights reserved.
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

import os
import shutil
from .common import test_result_collector as trc
from .mocks.mock_model_config import MockModelConfig

from model_analyzer.triton.model.model_config import ModelConfig
from model_analyzer.model_analyzer_exceptions \
    import TritonModelAnalyzerException


class TestModelConfig(trc.TestResultCollector):
    def setUp(self):
        self._model_config = {
            'name':
            'classification_chestxray_v1',
            'platform':
            'tensorflow_graphdef',
            'max_batch_size':
            32,
            'input': [{
                'name': 'NV_MODEL_INPUT',
                'data_type': 'TYPE_FP32',
                'format': 'FORMAT_NHWC',
                'dims': ['256', '256', '3']
            }],
            'output': [{
                'name': 'NV_MODEL_OUTPUT',
                'data_type': 'TYPE_FP32',
                'dims': ['15'],
                'label_filename': 'chestxray_labels.txt'
            }],
            'instance_group': [{
                'count': 1,
                'kind': 'KIND_GPU'
            }]
        }

        # Equivalent protobuf for the model config above.
        self._model_config_protobuf = """
name: "classification_chestxray_v1"
platform: "tensorflow_graphdef"
max_batch_size: 32
input [
  {
    name: "NV_MODEL_INPUT"
    data_type: TYPE_FP32
    format: FORMAT_NHWC
    dims: [256, 256, 3]
  }
]
output [
  {
    name: "NV_MODEL_OUTPUT"
    data_type: TYPE_FP32
    dims: [15]
    label_filename: "chestxray_labels.txt"
  }
]
instance_group [
  {
    kind: KIND_GPU
    count: 1
  }
]
"""

    def test_create_from_file(self):
        test_protobuf = self._model_config_protobuf
        mock_model_config = MockModelConfig(test_protobuf)
        mock_model_config.start()
        model_config = ModelConfig.create_from_file('/path/to/model_config')
        self.assertTrue(model_config.get_config() == self._model_config)
        mock_model_config.stop()

    def test_create_from_dict(self):
        model_config = ModelConfig.create_from_dictionary(self._model_config)
        self.assertTrue(model_config.get_config() == self._model_config)

        new_config = {'instance_group': [{'count': 2, 'kind': 'KIND_CPU'}]}
        model_config.set_config(new_config)
        self.assertTrue(model_config.get_config() == new_config)

    def test_write_config_file(self):
        model_config = ModelConfig.create_from_dictionary(self._model_config)
        model_output_path = os.path.abspath('./model_config')
        shutil.rmtree(model_output_path, ignore_errors=True)

        os.mkdir(model_output_path)

        model_output_path = os.path.abspath(model_output_path)
        model_config.write_config_to_file(model_output_path)

        model_config_from_file = \
            ModelConfig.create_from_file(model_output_path)
        self.assertTrue(
            model_config_from_file.get_config() == self._model_config)

        # output path is a file
        with self.assertRaises(TritonModelAnalyzerException):
            model_output_path += '/config.pbtxt'
            ModelConfig.create_from_file(model_output_path)

        # output path doesn't exist
        with self.assertRaises(TritonModelAnalyzerException):
            model_output_path = '/non/existent/path'
            ModelConfig.create_from_file(model_output_path)
