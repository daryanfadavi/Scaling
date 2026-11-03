from locust import HttpUser, LoadTestShape, between, task


class AppUser(HttpUser):
    wait_time = between(0.5, 1.0)

    @task
    def work(self):
        self.client.get("/work")


class RampShape(LoadTestShape):
    # (ends at second, users, spawn rate per second)
    stages = [
        (60, 10, 2),    # warm up
        (120, 30, 2),   # ramp
        (180, 60, 5),   # ramp
        (420, 60, 5),   # hold 4 min
        (480, 10, 5),   # drop
    ]

    def tick(self):
        run_time = self.get_run_time()
        for end, users, rate in self.stages:
            if run_time < end:
                return (users, rate)
        return None  # stop the test