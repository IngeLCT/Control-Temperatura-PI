from __future__ import annotations


class PIDController:
    """PID con derivada sobre la medición y anti-windup por integración condicional."""

    def __init__(
        self,
        kp: float,
        ki: float,
        kd: float,
        output_min: float = 0.0,
        output_max: float = 100.0,
    ) -> None:
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.output_min = output_min
        self.output_max = output_max
        self._integral = 0.0
        self._previous_measurement: float | None = None

    def reset(self) -> None:
        self._integral = 0.0
        self._previous_measurement = None

    def update(self, setpoint: float, measurement: float, dt: float) -> float:
        if dt <= 0:
            raise ValueError("dt debe ser mayor que cero")

        error = setpoint - measurement
        proportional = self.kp * error
        derivative = 0.0
        if self._previous_measurement is not None:
            derivative = -self.kd * (measurement - self._previous_measurement) / dt

        proposed_integral = self._integral + self.ki * error * dt
        raw_output = proportional + proposed_integral + derivative
        saturated_output = min(self.output_max, max(self.output_min, raw_output))

        saturation_pushes_outward = (
            raw_output > self.output_max and error > 0
        ) or (
            raw_output < self.output_min and error < 0
        )
        if not saturation_pushes_outward:
            self._integral = proposed_integral

        self._previous_measurement = measurement
        return saturated_output
