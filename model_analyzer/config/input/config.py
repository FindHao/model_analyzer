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

from .config_field import ConfigField
from .config_primitive import ConfigPrimitive
from .config_list_string import ConfigListString
from .config_list_numeric import ConfigListNumeric
from .config_object import ConfigObject
from .config_list_generic import ConfigListGeneric
from .config_union import ConfigUnion
from .config_none import ConfigNone

from .objects.config_model import ConfigModel
from .objects.config_plot import ConfigPlot
from model_analyzer.triton.server.server_config import \
    TritonServerConfig
from model_analyzer.perf_analyzer.perf_config import \
    PerfAnalyzerConfig
from model_analyzer.record.record import RecordType
from .config_sweep import ConfigSweep
from model_analyzer.model_analyzer_exceptions \
    import TritonModelAnalyzerException
from .config_enum import ConfigEnum
from .objects.config_protobuf_utils import \
    is_protobuf_type_primitive, protobuf_to_config_type

from tritonclient.grpc.model_config_pb2 import ModelConfig
from google.protobuf.descriptor import FieldDescriptor

import yaml
import logging
import os


class AnalyzerConfig:
    """
    Model Analyzer config object.
    """
    def __init__(self):
        """
        Create a new config.
        """

        self._fields = {}
        self._fill_config()

    def _add_config(self, config_field):
        """
        Add a new config field.

        Parameters
        ----------
        config_field : ConfigField
            Config field to be added

        Raises
        ------
        KeyError
            If the field already exists, it will raise this exception.
        """

        if config_field.name() not in self._fields:
            self._fields[config_field.name()] = config_field
        else:
            raise KeyError

    def _resolve_protobuf_field(self, field):
        """
        Recursively resolve protobuf fields.

        Parameters
        ----------
        field : google.protobuf.pyext._message.FieldDescriptor

        Returns
        -------
        ConfigValue
            A config type equivalent to the protobuf type.

        Raises
        ------
        TritonModelAnalyzerException
            If the protobuf config field cannot be resolved, this exception
            will be raised.
        """

        if is_protobuf_type_primitive(field.type):
            config_type = protobuf_to_config_type(field.type)

            # If it is a repeated field, we should use ConfigListGeneric
            if field.label == FieldDescriptor.LABEL_REPEATED:
                config_type = ConfigListGeneric(ConfigPrimitive(config_type))
            else:
                config_type = ConfigPrimitive(config_type)

        elif field.type == FieldDescriptor.TYPE_MESSAGE:
            # If the field type is TYPE_MESSAGE, we need to create a new
            # message of type ConfigObject
            sub_field_schema = {}

            fields = field.message_type.fields
            for sub_field in fields:
                sub_field_schema[
                    sub_field.name] = self._resolve_protobuf_field(sub_field)
            if field.label == FieldDescriptor.LABEL_REPEATED:
                config_type = ConfigListGeneric(
                    ConfigObject(schema=sub_field_schema))
            else:
                config_type = ConfigObject(schema=sub_field_schema)
        elif field.type == FieldDescriptor.TYPE_ENUM:
            choices = []
            enum_values = field.enum_type.values
            for enum_value in enum_values:
                choices.append(enum_value.name)
            config_type = ConfigEnum(choices)
        else:
            raise TritonModelAnalyzerException(
                'The current version of Model Config is not supported by Model Analyzer.'
            )

        return ConfigSweep(ConfigUnion([config_type, ConfigNone()]))

    def _get_model_config_fields(self):
        """
        Constructs a ConfigObject from the ModelConfig protobuf.
        """

        model_config_prototype = ModelConfig()
        fields = model_config_prototype.DESCRIPTOR.fields

        schema = {}
        for field in fields:
            schema[field.name] = self._resolve_protobuf_field(field)

        return ConfigObject(schema)

    def _fill_config(self):
        model_config_fields = self._get_model_config_fields()
        self._add_config(
            ConfigField('model_repository',
                        flags=['--model-repository', '-m'],
                        field_type=ConfigPrimitive(str, required=True),
                        description='Model repository location'))

        def objective_list_output_mapper(objectives):
            # Takes a list of objectives and maps them
            # into a dict
            output_dict = {}
            for objective in objectives:
                value = ConfigPrimitive(type_=int)
                value.set_value(10)
                output_dict[objective] = value
            return output_dict

        objectives_scheme = ConfigUnion([
            ConfigObject(
                schema={
                    tag: ConfigPrimitive(type_=int)
                    for tag in RecordType.get_all_record_types().keys()
                }),
            ConfigListString(output_mapper=objective_list_output_mapper)
        ])
        constraints_scheme = ConfigObject(
            schema={
                'perf_throughput':
                ConfigObject(schema={
                    'min': ConfigPrimitive(int),
                }),
                'perf_latency':
                ConfigObject(schema={
                    'max': ConfigPrimitive(int),
                }),
                'gpu_used_memory':
                ConfigObject(schema={
                    'max': ConfigPrimitive(int),
                }),
            })
        model_object_constraint = ConfigObject(
            required=True,
            schema={
                # Any key is allowed, but the keys must follow the pattern
                # below
                '*':
                ConfigObject(
                    schema={
                        'parameters':
                        ConfigObject(
                            schema={
                                'batch_sizes': ConfigListNumeric(type_=int),
                                'concurrency': ConfigListNumeric(type_=int)
                            }),
                        'objectives':
                        objectives_scheme,
                        'constraints':
                        constraints_scheme,
                        'model_config_parameters':
                        model_config_fields,
                        'perf_analyzer_flags':
                        ConfigObject(
                            schema={
                                k: ConfigPrimitive(type_=str)
                                for k in PerfAnalyzerConfig.allowed_keys()
                            })
                    })
            },
            output_mapper=ConfigModel.model_object_to_config_model)
        self._add_config(
            ConfigField(
                'model_names',
                flags=['--model-names', '-n'],
                field_type=ConfigUnion([
                    model_object_constraint,
                    ConfigListGeneric(
                        ConfigUnion([
                            model_object_constraint,
                            ConfigPrimitive(str,
                                            output_mapper=ConfigModel.
                                            model_str_to_config_model)
                        ]),
                        required=True,
                        output_mapper=ConfigModel.model_mixed_to_config_model),
                    ConfigListString(
                        output_mapper=ConfigModel.model_list_to_config_model),
                ],
                                       required=True),
                description=
                'Comma-delimited list of the model names to be profiled'))

        self._add_config(
            ConfigField(
                'objectives',
                field_type=objectives_scheme,
                default_value={'perf_throughput': 10},
                description=
                'Model Analyzer uses the objectives described here to find the best configuration for each model.'
            ))
        self._add_config(
            ConfigField(
                'constraints',
                field_type=constraints_scheme,
                description=
                'Constraints on the objectives specified in the "objectives" field of the config.'
            ))
        self._add_config(
            ConfigField(
                'batch_sizes',
                flags=['--batch-sizes', '-b'],
                field_type=ConfigListNumeric(int),
                default_value=1,
                description=
                'Comma-delimited list of batch sizes to use for the profiling')
        )
        self._add_config(
            ConfigField(
                'concurrency',
                flags=['-c', '--concurrency'],
                field_type=ConfigListNumeric(int),
                description=
                "Comma-delimited list of concurrency values or ranges <start:end:step>"
                " to be used during profiling"))
        self._add_config(
            ConfigField('perf_analyzer_timeout',
                        flags=['--perf-analyzer-timeout'],
                        field_type=ConfigPrimitive(int),
                        default_value=600,
                        description="Perf analyzer timeout value in seconds."))
        self._add_config(
            ConfigField(
                'perf_analyzer_cpu_util',
                flags=['--perf-analyzer-cpu-util'],
                field_type=ConfigPrimitive(float),
                default_value=80,
                description=
                "Maximum CPU utilization value allowed for the perf_analyzer.")
        )
        self._add_config(
            ConfigField(
                'run_config_search_max_concurrency',
                flags=['--run-config-search-max-concurrency'],
                field_type=ConfigPrimitive(int),
                default_value=1024,
                description=
                "Max concurrency value that run config search should not go beyond that."
            ))
        self._add_config(
            ConfigField(
                'run_config_search_max_instance_count',
                flags=['--run-config-search-max-instance-count'],
                field_type=ConfigPrimitive(int),
                default_value=5,
                description=
                "Max instance count value that run config search should not go beyond that."
            ))
        self._add_config(
            ConfigField('run_config_search_disable',
                        flags=['--run-config-search-disable'],
                        field_type=ConfigPrimitive(bool),
                        parser_args={'action': 'store_true'},
                        default_value=False,
                        description="Disable run config search."))
        self._add_config(
            ConfigField(
                'run_config_search_max_preferred_batch_size',
                flags=['--run-config-search-max-preferred-batch-size'],
                field_type=ConfigPrimitive(int),
                default_value=16,
                description=
                "Max preferred batch size value that run config search should not go beyond that."
            ))
        self._add_config(
            ConfigField('export',
                        flags=['--export'],
                        field_type=ConfigPrimitive(bool),
                        default_value=True,
                        description="Enables exporting metrics to a file"))
        self._add_config(
            ConfigField(
                'export_path',
                flags=['--export-path', '-e'],
                default_value='.',
                field_type=ConfigPrimitive(str),
                description=
                "Full path to directory in which to store the results"))
        self._add_config(
            ConfigField(
                'summarize',
                flags=['--summarize'],
                field_type=ConfigPrimitive(bool),
                default_value=True,
                description=
                'Model Analyzer generates a brief summary of the collected data.'
            ))
        self._add_config(
            ConfigField(
                'num_configs_per_model',
                field_type=ConfigPrimitive(int),
                default_value=3,
                description='The number of top model configurations to plot.'))
        self._add_config(
            ConfigField('num_top_model_configs',
                        field_type=ConfigPrimitive(int),
                        default_value=0,
                        description='The number of top models to save.'))

        self._add_config(
            ConfigField(
                'filename_model_inference',
                flags=['--filename-model-inference'],
                default_value='metrics-model-inference.csv',
                field_type=ConfigPrimitive(str),
                description=
                'Specifies filename for storing model inference metrics'))
        self._add_config(
            ConfigField(
                'filename_model_gpu',
                flags=['--filename-model-gpu'],
                field_type=ConfigPrimitive(str),
                default_value='metrics-model-gpu.csv',
                description='Specifies filename for storing model GPU metrics')
        )
        self._add_config(
            ConfigField(
                'filename_server_only',
                flags=['--filename-server-only'],
                field_type=ConfigPrimitive(str),
                default_value='metrics-server-only.csv',
                description='Specifies filename for server-only metrics'))
        self._add_config(
            ConfigField(
                'max_retries',
                flags=['-r', '--max-retries'],
                field_type=ConfigPrimitive(int),
                default_value=100,
                description=
                'Specifies the max number of retries for any retry attempt'))
        self._add_config(
            ConfigField(
                'duration_seconds',
                field_type=ConfigPrimitive(int),
                flags=['-d', '--duration-seconds'],
                default_value=5,
                description=
                'Specifies how long (seconds) to gather server-only metrics'))
        self._add_config(
            ConfigField(
                'monitoring_interval',
                flags=['-i', '--monitoring-interval'],
                field_type=ConfigPrimitive(float),
                default_value=0.01,
                description=
                'Interval of time between DGCM measurements in seconds'))
        self._add_config(
            ConfigField(
                'client_protocol',
                flags=['--client-protocol'],
                choices=['http', 'grpc'],
                field_type=ConfigPrimitive(str),
                default_value='grpc',
                description=
                'The protocol used to communicate with the Triton Inference Server'
            ))
        self._add_config(
            ConfigField(
                'perf_analyzer_path',
                flags=['--perf-analyzer-path'],
                field_type=ConfigPrimitive(str),
                default_value='perf_analyzer',
                description=
                'The full path to the perf_analyzer binary executable'))
        self._add_config(
            ConfigField(
                'perf_measurement_window',
                flags=['--perf-measurement-window'],
                field_type=ConfigPrimitive(int),
                default_value=5000,
                description=
                'Time interval in milliseconds between perf_analyzer measurements. perf_analyzer will take '
                'measurements over all the requests completed within this time interval.'
            ))
        self._add_config(
            ConfigField('perf_output',
                        flags=['--perf-output'],
                        field_type=ConfigPrimitive(bool),
                        default_value=False,
                        description=
                        'Enables the output from the perf_analyzer to stdout'))
        self._add_config(
            ConfigField(
                'triton_launch_mode',
                field_type=ConfigPrimitive(str),
                flags=['--triton-launch-mode'],
                default_value='local',
                choices=['local', 'docker', 'remote'],
                description="The method by which to launch Triton Server. "
                "'local' assumes tritonserver binary is available locally. "
                "'docker' pulls and launches a triton docker container with "
                "the specified version. 'remote' connects to a running "
                "server using given http, grpc and metrics endpoints. "))
        self._add_config(
            ConfigField('triton_docker_image',
                        flags=['--triton-docker-image'],
                        field_type=ConfigPrimitive(str),
                        default_value='nvcr.io/nvidia/tritonserver:21.03-py3',
                        description='Triton Server Docker image tag'))
        self._add_config(
            ConfigField(
                'triton_http_endpoint',
                default_value='localhost:8000',
                flags=['--triton-http-endpoint'],
                field_type=ConfigPrimitive(str),
                description=
                "Triton Server HTTP endpoint url used by Model Analyzer client. "
                "Will be ignored if server-launch-mode is not 'remote'"))
        self._add_config(
            ConfigField(
                'triton_grpc_endpoint',
                flags=['--triton-grpc-endpoint'],
                field_type=ConfigPrimitive(str),
                default_value='localhost:8001',
                description=
                "Triton Server HTTP endpoint url used by Model Analyzer client. "
                "Will be ignored if server-launch-mode is not 'remote'"))
        self._add_config(
            ConfigField(
                'triton_metrics_url',
                field_type=ConfigPrimitive(str),
                flags=['--triton-metrics-url'],
                default_value='http://localhost:8002/metrics',
                description="Triton Server Metrics endpoint url. "
                "Will be ignored if server-launch-mode is not 'remote'"))
        self._add_config(
            ConfigField('triton_server_path',
                        field_type=ConfigPrimitive(str),
                        flags=['--triton-server-path'],
                        default_value='tritonserver',
                        description=
                        'The full path to the tritonserver binary executable'))
        self._add_config(
            ConfigField(
                'triton_output_path',
                field_type=ConfigPrimitive(str),
                flags=['--triton-output-path'],
                description=
                'The full path to a file to write the Triton Server log output to.'
            ))
        self._add_config(
            ConfigField(
                'triton_server_flags',
                field_type=ConfigObject(
                    schema={
                        k: ConfigPrimitive(str)
                        for k in TritonServerConfig.server_arg_keys
                    }),
                description=
                'Allows custom configuration of the triton instances used by model analyzer.'
            ))
        self._add_config(
            ConfigField('log_level',
                        flags=['--log-level'],
                        default_value='INFO',
                        field_type=ConfigPrimitive(str),
                        choices=['INFO', 'DEBUG', 'ERROR', 'WARNING'],
                        description='Logging levels'))
        self._add_config(
            ConfigField(
                'gpus',
                flags=['--gpus'],
                field_type=ConfigListString(),
                default_value='all',
                description="List of GPU UUIDs to be used for the profiling. "
                "Use 'all' to profile all the GPUs visible by CUDA."))
        self._add_config(
            ConfigField(
                'output_model_repository_path',
                field_type=ConfigPrimitive(str),
                default_value='./output_model_repository',
                flags=['--output-model-repository-path'],
                description=
                'Output model repository path used by Model Analyzer.'
                ' This is the directory that will contain all the generated model configurations'
            ))
        self._add_config(
            ConfigField(
                'override_output_model_repository',
                field_type=ConfigPrimitive(bool),
                parser_args={'action': 'store_true'},
                default_value=False,
                flags=['--override-output-model-repository'],
                description=
                'Will override the contents of the output model repository'
                ' and replace it with the new results.'))
        self._add_config(
            ConfigField('config_file',
                        field_type=ConfigPrimitive(str),
                        flags=['-f', '--config-file'],
                        description="Path to Model Analyzer Config File."))
        self._add_config(
            ConfigField('inference_output_fields',
                        field_type=ConfigListString(),
                        default_value=[
                            'model_name', 'batch_size', 'concurrency',
                            'model_config_path', 'instance_group',
                            'dynamic_batch_sizes', 'satisfies_constraints',
                            'perf_throughput', 'perf_latency', 'cpu_used_ram'
                        ]))
        self._add_config(
            ConfigField('gpu_output_fields',
                        field_type=ConfigListString(),
                        default_value=[
                            'model_name', 'gpu_id', 'batch_size',
                            'concurrency', 'model_config_path',
                            'instance_group', 'dynamic_batch_sizes',
                            'satisfies_constraints', 'gpu_used_memory',
                            'gpu_utilization', 'gpu_power_usage'
                        ]))
        self._add_config(
            ConfigField('server_output_fields',
                        field_type=ConfigListString(),
                        default_value=[
                            'model_name', 'gpu_id', 'gpu_used_memory',
                            'gpu_utilization', 'gpu_power_usage'
                        ]))
        self._add_plot_config()

    def _preprocess_and_verify_arguments(self):
        """
        Enforces some rules on the config.

        Raises
        ------
        TritonModelAnalyzerException
            If there is a problem with arguments or config.
        """

        if self.export:
            if not self.export_path:
                logging.warning(
                    "--export-path specified without --export flag: skipping exporting metrics."
                )
                self.export_path = None
            elif self.export_path and not os.path.isdir(self.export_path):
                raise TritonModelAnalyzerException(
                    f"Export path {self.export_path} is not a directory.")
        if self.triton_launch_mode == 'remote':
            if self.client_protocol == 'http' and not self.triton_http_endpoint:
                raise TritonModelAnalyzerException(
                    "client-protocol is 'http'. Must specify triton-http-endpoint "
                    "if connecting to already running server or change protocol using "
                    "--client-protocol.")
            if self.client_protocol == 'grpc' and not self.triton_grpc_endpoint:
                raise TritonModelAnalyzerException(
                    "client-protocol is 'grpc'. Must specify triton-grpc-endpoint "
                    "if connecting to already running server or change protocol using "
                    "--client-protocol.")

        # If run config search is disabled and no concurrency value is provided,
        # set the default value.
        if self.run_config_search_disable:
            if len(self.concurrency) == 0:
                self.concurrency = [1]

    def _load_config_file(self, file_path):
        """
        Load YAML config

        Parameters
        ----------
        file_path : str
            Path to the Model Analyzer config file
        """

        with open(file_path, 'r') as config_file:
            config = yaml.load(config_file, Loader=yaml.FullLoader)
            return config

    def _setup_logger(self):
        """
        Setup logger format
        """
        log_level = logging.getLevelName(self.log_level)
        logging.basicConfig(level=log_level,
                            format="%(asctime)s.%(msecs)d %(levelname)-4s"
                            "[%(filename)s:%(lineno)d] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")

    def _autofill_values(self):
        """
        Fill in the implied or default
        config values.
        """

        new_model_names = {}
        for model in self.model_names:
            new_model = {}

            # Objectives
            if not model.objectives():
                new_model['objectives'] = self.objectives
            else:
                new_model['objectives'] = model.objectives()

            # Constraints
            if not model.constraints():
                if 'constraints' in self._fields and self._fields[
                        'constraints'].value():
                    new_model['constraints'] = self.constraints
            else:
                new_model['constraints'] = model.constraints()

            # Run parameters
            if not model.parameters():
                new_model['parameters'] = {
                    'batch_sizes': self.batch_sizes,
                    'concurrency': self.concurrency
                }
            elif 'batch_sizes' not in model.parameters():
                new_model['parameters'] = {
                    'batch_sizes': self.batch_sizes,
                    'concurrency': model.parameters()['concurrency']
                }
            elif 'concurrency' not in model.parameters():
                new_model['parameters'] = {
                    'batch_sizes': model.parameters()['batch_sizes'],
                    'concurrency': self.concurrency
                }
            else:
                new_model['parameters'] = model.parameters()

            # Transfer model config parameters and perf_analyzer_flags directly
            if model.model_config_parameters():
                new_model[
                    'model_config_parameters'] = model.model_config_parameters(
                    )
            if model.perf_analyzer_flags():
                new_model['perf_analyzer_flags'] = model.perf_analyzer_flags()

            new_model_names[model.model_name()] = new_model
        self._fields['model_names'].set_value(new_model_names)

    def _add_plot_config(self):
        """
        Sets up the plots that will be included in the summary
        """

        plots_scheme = ConfigObject(schema={
            '*':
            ConfigObject(
                schema={
                    'title': ConfigPrimitive(type_=str),
                    'x_axis': ConfigPrimitive(type_=str),
                    'y_axis': ConfigPrimitive(type_=str),
                    'monotonic': ConfigPrimitive(type_=bool)
                })
        },
                                    output_mapper=ConfigPlot.from_object)
        self._add_config(
            ConfigField(
                'plots',
                field_type=ConfigUnion([
                    plots_scheme,
                    ConfigListGeneric(type_=plots_scheme,
                                      output_mapper=ConfigPlot.from_list)
                ]),
                default_value={
                    'throughput_v_latency': {
                        'title': 'Throughput vs. Latency',
                        'x_axis': 'perf_latency',
                        'y_axis': 'perf_throughput',
                        'monotonic': True
                    },
                    'gpu_mem_v_latency': {
                        'title': 'GPU Memory vs. Latency',
                        'x_axis': 'perf_latency',
                        'y_axis': 'gpu_used_memory',
                        'monotonic': False
                    }
                },
                description=
                'Model analyzer uses the information in this section to construct plots of the results.'
            ))

    def set_config_values(self, args):
        """
        Set the config values. This function sets all the values for the
        config. CLI arguments have the highest priority, then YAML config
        values and then default values.

        Parameters
        ----------
        args : argparse.Namespace
            Parsed arguments from the CLI

        Raises
        ------
        TritonModelAnalyzerException
            If the required fields are not specified, it will raise
            this exception
        """

        # Config file has been specified
        if 'config_file' in args:
            yaml_config = self._load_config_file(args.config_file)
        else:
            yaml_config = None
        for key, value in self._fields.items():
            self._fields[key].set_name(key)
            if key in args:
                self._fields[key].set_value(getattr(args, key))
            elif yaml_config is not None and key in yaml_config:
                self._fields[key].set_value(yaml_config[key])
            elif value.default_value() is not None:
                self._fields[key].set_value(value.default_value())
            elif value.required():
                flags = ', '.join(value.flags())
                raise TritonModelAnalyzerException(
                    f'Config for {value.name()} is not specified. You need to specify it using the YAML config file or using the {flags} flags in CLI.'
                )
        self._setup_logger()
        self._preprocess_and_verify_arguments()
        self._autofill_values()

    def get_config(self):
        """
        Get the config dictionary.

        Returns
        -------
        dict
            Returns a dictionary where the keys are the
            configuration name and the values are ConfigField objects.
        """

        return self._fields

    def get_all_config(self):
        """
        Get a dictionary containing all the configurations.

        Returns
        -------
        dict
            A dictionary containing all the configurations.
        """

        config_dict = {}
        for config in self._fields.values():
            config_dict[config.name()] = config.value()

        return config_dict

    def __getattr__(self, name):
        return self._fields[name].value()