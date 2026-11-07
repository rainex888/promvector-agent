from ..config import ModbusSource
from ..models import Metric, Sample
from . import Collection


class Modbus:
    def __init__(self, source: ModbusSource):
        self.source = source

    async def read(self):
        from pymodbus.client import AsyncModbusTcpClient

        source = self.source
        client = AsyncModbusTcpClient(source.host, port=source.port, timeout=3, retries=1)
        try:
            if not await client.connect():
                raise ConnectionError("Modbus TCP endpoint is unavailable")
            metrics, metadata = {}, {}
            failures = 0
            for name, register in source.registers.items():
                try:
                    reader = (
                        client.read_holding_registers
                        if register.table == "holding"
                        else client.read_input_registers
                    )
                    count = int(register.data_type[-2:]) // 16
                    response = await reader(register.address, count=count, device_id=source.device_id)
                    if response.isError() or len(response.registers) != count:
                        raise ValueError("Invalid Modbus response")
                    decoded = client.convert_from_registers(
                        response.registers,
                        client.DATATYPE[register.data_type],
                        word_order=register.word_order,
                    )
                    metrics[name] = Metric(value=decoded, unit=register.unit)
                    metadata[f"register:{name}"] = f"{register.table}:{register.address}"
                    metadata[f"raw:{name}"] = ",".join(map(str, response.registers))
                except Exception:
                    failures += 1
            return metrics, metadata, failures
        finally:
            client.close()

    async def test(self):
        _, _, failures = await self.read()
        if failures:
            raise ValueError("Some configured registers could not be read")

    async def collect(self):
        metrics, metadata, failures = await self.read()
        if metrics:
            yield Collection([Sample(metrics=metrics, source_metadata=metadata)])
        if failures:
            raise ValueError(f"{failures} Modbus register(s) could not be read")
