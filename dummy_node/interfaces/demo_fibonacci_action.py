"""예시: Action Server 인터페이스.

`~/fibonacci` 액션으로 피보나치 수열을 계산하며 feedback을 발행한다.
cancel 요청과 중간 feedback을 모두 지원한다.
새 action을 만들려면 이 파일을 복사해서 액션명/타입/execute 로직을 바꾸면 된다.
"""

import time

from example_interfaces.action import Fibonacci
from rclpy.action import ActionServer, CancelResponse, GoalResponse

from dummy_node.registry import InterfaceBase, register


@register
class DemoFibonacciAction(InterfaceBase):
    name = "demo_fibonacci_action"

    action = "~/fibonacci"
    step_delay_sec = 0.5  # 각 항 계산 사이 지연(테스트에서 관측 가능하도록).

    def setup(self) -> None:
        self._server = ActionServer(
            self.node,
            Fibonacci,
            self.action,
            execute_callback=self._execute,
            goal_callback=self._on_goal,
            cancel_callback=self._on_cancel,
            callback_group=self.callback_group,
        )

    def _on_goal(self, goal_request) -> GoalResponse:
        self.log.info(f"[{self.name}] goal 수신: order={goal_request.order}")
        return GoalResponse.ACCEPT

    def _on_cancel(self, goal_handle) -> CancelResponse:
        self.log.info(f"[{self.name}] cancel 요청 수신")
        return CancelResponse.ACCEPT

    def _execute(self, goal_handle):
        order = goal_handle.request.order
        feedback = Fibonacci.Feedback()
        feedback.sequence = [0, 1]

        for i in range(1, order):
            if goal_handle.is_cancel_requested:
                goal_handle.canceled()
                self.log.info(f"[{self.name}] 취소됨")
                result = Fibonacci.Result()
                result.sequence = feedback.sequence
                return result

            feedback.sequence.append(
                feedback.sequence[i] + feedback.sequence[i - 1]
            )
            goal_handle.publish_feedback(feedback)
            time.sleep(self.step_delay_sec)

        goal_handle.succeed()
        result = Fibonacci.Result()
        result.sequence = feedback.sequence
        self.log.info(f"[{self.name}] 완료: {list(result.sequence)}")
        return result
