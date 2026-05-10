"""Textual 화면 (Screens) 모음.

각 화면은 자체 keybinding 과 lifecycle 을 가진다. 공유 상태는 App 의
SessionState 를 통해서만 주고받는다 — 화면 사이에 직접 인스턴스
참조를 보관하지 않는다.
"""
