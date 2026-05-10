"""whooing_tui 의 사용자 정의 위젯들.

CL #51126+ 부터 분리. 종전엔 모든 위젯이 screens/ 안 modal 로 살았으나,
화면 간 재사용 (MenuBar 등) 이 필요한 작은 컴포넌트는 별도 패키지.
"""

from whooing_tui.widgets.confirm import ConfirmModal
from whooing_tui.widgets.input_modal import InputModal, TextAreaModal
from whooing_tui.widgets.menubar import (
    MenuBar,
    MenuBarMixin,
    MenuItem,
    MenuPopup,
    MenuSpec,
    menubar_bindings,
)

__all__ = [
    # MenuBar 계열 — CL #51126+.
    "MenuBar", "MenuBarMixin", "MenuItem", "MenuPopup", "MenuSpec",
    "menubar_bindings",
    # 통합 modal — CL #51156+.
    "InputModal", "TextAreaModal", "ConfirmModal",
]
