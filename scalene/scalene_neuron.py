import json
import subprocess
import threading
import time
import tempfile
import os

from functools import cache
from typing import Tuple

class NeuronMonitor:
    
    def __init__(self) -> None:
        self._config_path = self._generate_config()
        self._process = subprocess.Popen(
            ['neuron-monitor', '-c', self._config_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self._lock = threading.Lock()
        self._line = ""
        self._thread = threading.Thread(target=self._update_line)
        self._thread.daemon = True
        self._thread.start()

    def __del__(self) -> None:
        self._process.kill()
        self._process.terminate()
        if os.path.exists(self._config_path):
            os.remove(self._config_path)

    def _generate_config(self) -> None:
        config = {
            "period": "1s",
            "system_metrics": [
                {
                    "type": "vcpu_usage"
                },
                {
                    "type": "memory_info"
                }
            ],
            "neuron_runtimes": [
                {
                    "tag_filter": ".*",
                    "metrics": [
                        {
                            "type": "neuroncore_counters"
                        },
                        {
                            "type": "memory_used"
                        },
                        {
                            "type": "neuron_runtime_vcpu_usage"
                        }
                    ]
                }
            ]
        }
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.json')
        with open(temp_file.name, 'w') as file:
            json.dump(config, file)
        return temp_file.name

    def _update_line(self) -> None:
        while True:
            newline = self._process.stdout.readline().strip()
            self._lock.acquire()
            self._line = newline
            self._lock.release()
        
    def readline(self) -> str:
        while True:
            self._lock.acquire()
            line = self._line
            self._lock.release()
            if line:
                return line

    
class ScaleneNeuron:

    def __init__(self) -> None:
        self._neuron_monitor = NeuronMonitor()
        self.cpu_utilization = 0.0
        self.memory_used_bytes = 0.0
        self.neuroncore_utilization = 0.0

    @cache
    def has_gpu(self) -> bool:
        try:
            result = subprocess.run(['neuron-ls'], capture_output=True, text=True, check=True)
            if "No neuron devices found" in result.stdout:
                return False
            else:
                return True
        except subprocess.CalledProcessError as e:
            # print(f"Error running neuron-ls: {e}")
            return False
        except FileNotFoundError:
            # print("neuron-ls command not found. Ensure AWS Neuron SDK is installed.")
            return False

    def nvml_reinit(self) -> None:
        """Here for compatibility with ScaleneGPU."""
        pass
    
    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def get_stats(self) -> Tuple[float, float]:
        line = self._neuron_monitor.readline()
        if line:
            self._parse_output(line)
        return self.neuroncore_utilization, self.memory_used_bytes / 1048576.0
    
    def _parse_output(self, output: str) -> None:
        try:
            data = json.loads(output)
            system_data = data.get('system_data', {})
            vcpu_usage = system_data.get('vcpu_usage', {})
            memory_info = system_data.get('memory_info', {})
            neuron_runtime_data = data.get('neuron_runtime_data', [])
            
            if vcpu_usage:
                total_idle = 0
                total_cores = 0
                for core, usage in vcpu_usage.get('usage_data', {}).items():
                    total_idle += usage.get('idle', 0)
                    total_cores += 1
                if total_cores > 0:
                    average_idle = total_idle / total_cores
                    self.cpu_utilization = 100 - average_idle

            # Disabled for now: host memory consumption
            # if memory_info:
            #    self.memory_used_bytes = memory_info.get('memory_used_bytes', 0)

            self.neuroncore_utilization = 0.0
            self.memory_used_bytes = 0.0
            
            if neuron_runtime_data:
                for per_core_info in neuron_runtime_data:
                    report = per_core_info.get('report', {})
                    neuroncore_counters = report.get('neuroncore_counters', {})
                    neuroncores_in_use = neuroncore_counters.get('neuroncores_in_use', {})

                    total_utilization = 0
                    total_neuroncores = 0

                    for core, counters in neuroncores_in_use.items():
                        total_utilization += counters.get('neuroncore_utilization', 0)
                        total_neuroncores += 1

                    if total_neuroncores > 0:
                        overall_utilization = total_utilization / total_neuroncores
                    else:
                        overall_utilization = 0
                        
                if total_neuroncores > 0:
                    average_utilization = (total_utilization / total_neuroncores) / 100
                    self.neuroncore_utilization = average_utilization

                total_memory_used = 0.0
                for per_core_info in neuron_runtime_data:
                    report = per_core_info.get('report', {})
                    memory_info = (report
                                   .get('memory_used', {})
                                   .get('neuron_runtime_used_bytes', {})
                                   .get('usage_breakdown', {})
                                   .get('neuroncore_memory_usage', {}))
                    for core, mem_info in memory_info.items():
                        total_memory_used += sum(mem_info.values())
                        
                self.memory_used_bytes = total_memory_used

        except json.JSONDecodeError as e:
            print(f"Error decoding JSON: {e}")
        except Exception as e:
            print(f"Unexpected error: {e}")

if __name__ == "__main__":
    monitor = ScaleneNeuron()
    try:
        while True:
            print(monitor.get_stats())
            time.sleep(0.1)
    except KeyboardInterrupt:
        monitor.stop()
