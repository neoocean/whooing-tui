"""ScanMixin — 중복/반복 거래 스캔 worker 클러스터.

감사 2026-06 (god 모듈 축소): EntriesScreen 에서 분리한 ~480줄. 메서드는
self(EntriesScreen)의 set_status / _client / app / _entries / _last_*_scan_days
등에 의존하므로 mixin 으로 둔다 — *동작 변경 없이* 위치만 이동. 무거운 의존
(repo / 모달 / core 휴리스틱)은 각 메서드 내부 lazy import 그대로.
"""

from __future__ import annotations

import logging

from textual import work

from whooing_tui.dates import days_ago_yyyymmdd, today_yyyymmdd

log = logging.getLogger(__name__)


class ScanMixin:
    """중복 검사(scan_duplicates) + 반복 누락 검사(scan_recurring) worker."""

    def action_scan_duplicates(self) -> None:
        """CL #52963+: 지난 3년 거래 일괄 중복 스캔 (입력 메뉴).

        사용자 요청 — 거래내력에 중복 항목이 늘어났으니 메뉴로 한 번에
        스캔해 cluster 마다 삭제/보존 선택. 실제 흐름은 worker 안.

        CL #52968+: 동기 진입점에서 즉시 status 갱신 — 사용자가 메뉴 클릭
        후 worker 가 schedule 되는 동안에도 "스캔 시작" 피드백을 즉시 본다.
        worker 자체는 `_scan_duplicates_worker` 의 첫 await 에서 비로소
        실행되므로, sync 단계에서 status 를 미리 set 해 두면 사용자가
        "눌러도 아무 반응 없음" 으로 오인할 여지 없음.
        """
        log.info("action_scan_duplicates invoked")
        self.set_status("⏳ 중복 거래 검사 시작 — 거래 fetch 중…")
        self._scan_duplicates_worker()

    @work(exclusive=True, group="dupe_scan", name="scan_duplicates")
    async def _scan_duplicates_worker(self) -> None:
        """범위 선택 → 거래 fetch → cluster 추출 → sqlite 영구화 → 2단계 UI.

        CL #53006+: 사용자가 1개월/3개월/6개월/1년/3년/5년 중 선택
        (`DupeScanRangeModal`). 각 범위는 별도 sqlite cache slot 이라
        번갈아 검사 가능. Esc 면 wizard 취소.

        CL #52989+: sqlite 캐싱 + 2단계 UI. 흐름:

          1) range modal push → 사용자가 일수 선택 (Esc = 취소).
          2) repo.has_open_scan(...) → 같은 (section, range) 의 pending 이
             있으면 cache hit → fetch skip → overview push.
          3) cache miss 면 fetch + cluster 분석 → repo.save_scan → overview.
          4) overview 의 F5 시 refresh_callback 이 worker 로직 재호출
             (clear + fetch + save + 새 list 반환).

        CL #52977+: fetch / 분석 단계 동안 `ScanProgressModal` 진행 안내.
        """
        import asyncio
        from whooing_tui.dupe_scan_repo import DupeScanRepository
        from whooing_tui.screens.duplicate_scan import DupeScanRangeModal
        from whooing_tui.screens.dupe_scan_overview import (
            DupeScanOverviewScreen,
        )

        log.info("scan_duplicates worker started")
        session = self.app.session  # type: ignore[attr-defined]
        if not session.section_id:
            self.set_status(
                "활성 섹션이 없습니다 — `s` 로 먼저 선택하세요.", error=True,
            )
            return

        # 1) 범위 선택 — Esc 면 wizard 취소.
        days = await self.app.push_screen_wait(  # type: ignore[attr-defined]
            DupeScanRangeModal(default_days=self._last_dupe_scan_days),
        )
        if days is None:
            self.set_status("중복 검사 취소.")
            return
        # 다음 진입 시 같은 항목 highlight.
        self._last_dupe_scan_days = int(days)

        repo = DupeScanRepository()
        end_date = today_yyyymmdd()
        start_date = days_ago_yyyymmdd(int(days))

        # 1) 캐시 hit 검사.
        cached_clusters = repo.load_all_clusters(
            section_id=session.section_id,
            range_start=start_date, range_end=end_date,
        )
        has_pending = any(c.status == "pending" for c in cached_clusters)

        # delete callback — entries 삭제 + 로컬 sqlite annotation purge.
        async def _delete_many(eids: list[str]) -> tuple[int, list[str]]:
            deleted = 0
            failed: list[str] = []
            for eid in eids:
                try:
                    await self._client.delete_entry(
                        section_id=session.section_id, entry_id=eid,
                    )
                    deleted += 1
                    try:
                        self._purge_local(eid)
                    except Exception:  # pragma: no cover
                        log.exception("purge_local %s failed", eid)
                except ToolError as e:
                    failed.append(f"{eid} [{e.kind}] {e.message}")
                except Exception as e:  # pragma: no cover
                    log.exception("dupe scan delete %s failed", eid)
                    failed.append(f"{eid} INTERNAL: {e}")
            return deleted, failed

        # refresh callback — F5 시 호출. clear → fetch → save → 새 list.
        async def _refresh() -> list:
            return await self._fetch_and_save_dupe_clusters(
                repo=repo, session=session,
                range_start=start_date, range_end=end_date,
            )

        if has_pending:
            self.set_status(
                f"💾 sqlite 캐시 hit — pending {sum(1 for c in cached_clusters if c.status=='pending')} 건 "
                f"({start_date} ~ {end_date}). F5 로 새로고침.",
            )
            clusters_for_overview = cached_clusters
            cached_flag = True
        else:
            # 2) cache miss — fetch + save.
            clusters_for_overview = await self._fetch_and_save_dupe_clusters(
                repo=repo, session=session,
                range_start=start_date, range_end=end_date,
            )
            cached_flag = False
            if not clusters_for_overview:
                # 0건이면 overview 도 띄우지 않음 — status 만.
                return

        result = await self.app.push_screen_wait(  # type: ignore[attr-defined]
            DupeScanOverviewScreen(
                clusters_for_overview,
                repo=repo,
                section_id=session.section_id,
                range_start=start_date, range_end=end_date,
                client=self._client,
                session=session,
                delete_callback=_delete_many,
                refresh_callback=_refresh,
                cached=cached_flag,
            ),
        )
        if result:
            self._selected_entry_ids.clear()
            self.set_status("중복 검사 완료 — 재로드 중…")
            self.refresh_entries()
        else:
            self.set_status("중복 검사 종료.")

    async def _fetch_and_save_dupe_clusters(
        self,
        *,
        repo,
        session,
        range_start: str,
        range_end: str,
    ) -> list:
        """후잉 entries 조회 → find_duplicate_clusters → repo.save_scan.

        worker (_scan_duplicates_worker) 와 refresh_callback 모두에서 사용.
        진행 안내는 ScanProgressModal 을 띄워 단계별 set_activity.
        실패 시 (set_status + 빈 list 반환).
        """
        import asyncio
        from whooing_core.dupes import find_duplicate_clusters
        from whooing_tui.screens.duplicate_scan import ScanProgressModal

        # refresh 흐름에서는 caller 가 먼저 sqlite 의 기존 row 를 비워야
        # save_scan 이 새로 INSERT 후 깨끗한 상태가 된다.
        try:
            repo.clear_scan(
                section_id=session.section_id,
                range_start=range_start, range_end=range_end,
            )
        except Exception:  # pragma: no cover
            log.exception("clear_scan failed (계속 진행)")

        progress = ScanProgressModal(initial="📊 거래 fetch 시작…")
        self.app.push_screen(progress)  # type: ignore[attr-defined]
        await asyncio.sleep(0)

        # CL #53010+: 사용자가 진행을 *계속* 보도록 chunk 단위 콜백.
        # client.list_entries 가 yearly 분할 / 100-cap bisect 마다 호출.
        # 누적 received 건수 유지 — closure 안 list 로 mutable.
        running_total = [0]   # received 누적
        bisect_count = [0]
        chunk_count = [0]
        last_range = [range_start, range_end]

        def _on_fetch_progress(kind: str, start: str, end: str, **extra: Any) -> None:
            last_range[0], last_range[1] = start, end
            try:
                if kind == "yearly":
                    idx = extra.get("range_idx") or 1
                    total = extra.get("range_total") or 1
                    progress.set_activity(
                        f"📦 연도별 분할 — 구간 {idx}/{total}\n"
                        f"{start} ~ {end}\n"
                        f"받은 거래 누적 {running_total[0]:,} 건",
                    )
                elif kind == "fetch":
                    chunk_count[0] += 1
                    progress.set_activity(
                        f"📡 후잉 요청 #{chunk_count[0]} → /entries.json\n"
                        f"{start} ~ {end}\n"
                        f"받은 거래 누적 {running_total[0]:,} 건",
                    )
                elif kind == "received":
                    n = int(extra.get("count") or 0)
                    running_total[0] += n
                    cap_note = " ⚠️  100건 cap 도달 — 재분할" if n >= 100 else ""
                    progress.set_activity(
                        f"📥 받음 {n} 건{cap_note}\n"
                        f"{start} ~ {end}\n"
                        f"받은 거래 누적 {running_total[0]:,} 건 · "
                        f"요청 {chunk_count[0]} 회",
                    )
                elif kind == "bisect":
                    bisect_count[0] += 1
                    mid = extra.get("mid", "?")
                    progress.set_activity(
                        f"🔀 100건 한도 → 분할 재요청 #{bisect_count[0]}\n"
                        f"{start} ~ {mid}  +  {extra.get('next_start', '?')} ~ {end}\n"
                        f"받은 거래 누적 {running_total[0]:,} 건",
                    )
                elif kind == "cache_hit":
                    n = int(extra.get("total") or 0)
                    progress.set_activity(
                        f"💾 캐시 hit — {n:,} 건 즉시 로드\n{start} ~ {end}",
                    )
                elif kind == "done":
                    n = int(extra.get("total") or 0)
                    progress.set_activity(
                        f"✅ fetch 완료 — 총 {n:,} 건 수신\n"
                        f"({range_start} ~ {range_end} · "
                        f"요청 {chunk_count[0]} 회 · bisect {bisect_count[0]} 회)",
                    )
            except Exception:  # pragma: no cover
                log.exception("fetch progress callback failed")

        try:
            progress.set_activity(
                f"📊 거래 fetch 시작…\n{range_start} ~ {range_end}",
            )
            self.set_status(
                f"⏳ 중복 검사 — 거래 fetch 중… ({range_start} ~ {range_end})",
            )
            await asyncio.sleep(0)
            try:
                entries = await self._client.list_entries(
                    section_id=session.section_id,
                    start_date=range_start, end_date=range_end,
                    on_progress=_on_fetch_progress,
                )
            except ToolError as e:
                self.set_status(
                    f"거래 조회 실패 [{e.kind}] {e.message}", error=True,
                )
                return []
            except Exception as e:
                log.exception("scan_duplicates fetch failed")
                self.set_status(f"거래 조회 실패 (INTERNAL): {e}", error=True)
                return []
            progress.set_activity(
                f"🔍 중복 cluster 검색 중…\n"
                f"거래 {len(entries):,} 건 분석 (blocking + windowing)\n"
                f"|금액| bucket → ±7일 window → union-find",
            )
            # CPU-bound 스캔을 thread 로 offload — 이벤트 루프 freeze 방지
            # (감사 2026-06 3-A). 함수는 순수라 thread-safe.
            clusters = await asyncio.to_thread(
                find_duplicate_clusters, entries, date_window_days=7,
            )
            if not clusters:
                self.set_status(
                    f"✅ 거래 {len(entries):,} 건 검사 완료 — 중복 후보 없음.",
                )
                return []
            progress.set_activity(
                f"💾 결과 저장 중…\n"
                f"{len(clusters)} 개 cluster sqlite 영구화\n"
                f"(테이블: dupe_scan_clusters · status=pending)",
            )
            await asyncio.sleep(0)
            stored = repo.save_scan(
                section_id=session.section_id,
                range_start=range_start, range_end=range_end,
                clusters=clusters,
            )
            self.set_status(
                f"🔍 중복 cluster {len(stored)} 개 발견 — 검토 시작.",
            )
            return stored
        finally:
            try:
                progress.dismiss()
            except Exception:  # pragma: no cover
                pass

    def action_scan_recurring(self) -> None:
        """반복거래누락 검사 (입력 메뉴) — 동기 진입점에서 즉시 status 갱신."""
        log.info("action_scan_recurring invoked")
        self.set_status("⏳ 반복 거래 누락 검사 시작 — 거래 fetch 중…")
        self._scan_recurring_worker()

    @work(exclusive=True, group="recurring_scan", name="scan_recurring")
    async def _scan_recurring_worker(self) -> None:
        """범위 선택 → 거래 fetch → 누락 시리즈 추출 → sqlite 영구화 → overview.

        중복 검사(`_scan_duplicates_worker`) 와 대칭 — 같은 sqlite 캐싱 정책.
        거래를 생성/삭제하지 않으므로 delete_callback 은 없고, 상태 변경
        (handled/dismissed) 은 overview 가 repo 에 직접 기록한다.
        """
        from whooing_tui.recurring_scan_repo import RecurringScanRepository
        from whooing_tui.screens.recurring_scan import (
            RecurringOmissionScreen,
            RecurringScanRangeModal,
        )

        session = self.app.session  # type: ignore[attr-defined]
        if not session.section_id:
            self.set_status(
                "활성 섹션이 없습니다 — `s` 로 먼저 선택하세요.", error=True,
            )
            return

        days = await self.app.push_screen_wait(  # type: ignore[attr-defined]
            RecurringScanRangeModal(default_days=self._last_recurring_scan_days),
        )
        if days is None:
            self.set_status("반복 거래 누락 검사 취소.")
            return
        self._last_recurring_scan_days = int(days)

        repo = RecurringScanRepository()
        end_date = today_yyyymmdd()
        start_date = days_ago_yyyymmdd(int(days))

        cached_series = repo.load_all_series(
            section_id=session.section_id,
            range_start=start_date, range_end=end_date,
        )
        has_pending = any(s.status == "pending" for s in cached_series)

        async def _refresh() -> list:
            return await self._fetch_and_save_recurring(
                repo=repo, session=session,
                range_start=start_date, range_end=end_date,
            )

        if has_pending:
            n_pending = sum(1 for s in cached_series if s.status == "pending")
            self.set_status(
                f"💾 sqlite 캐시 hit — 남은 누락 {n_pending} 건 "
                f"({start_date} ~ {end_date}). F5 로 새로고침.",
            )
            series_for_overview = cached_series
            cached_flag = True
        else:
            series_for_overview = await self._fetch_and_save_recurring(
                repo=repo, session=session,
                range_start=start_date, range_end=end_date,
            )
            cached_flag = False
            if not series_for_overview:
                return

        result = await self.app.push_screen_wait(  # type: ignore[attr-defined]
            RecurringOmissionScreen(
                series_for_overview,
                repo=repo,
                section_id=session.section_id,
                range_start=start_date, range_end=end_date,
                session=session,
                refresh_callback=_refresh,
                cached=cached_flag,
            ),
        )
        if result:
            self.set_status("반복 거래 누락 검사 완료.")
        else:
            self.set_status("반복 거래 누락 검사 종료.")

    async def _fetch_and_save_recurring(
        self,
        *,
        repo,
        session,
        range_start: str,
        range_end: str,
    ) -> list:
        """후잉 entries 조회 → detect_recurring_omissions → repo.save_scan.

        worker 와 refresh_callback 모두에서 사용. 진행 안내는
        RecurringScanProgressModal. 실패 시 set_status + 빈 list.
        """
        import asyncio
        from whooing_core.recurring import detect_recurring_omissions
        from whooing_tui.screens.recurring_scan import (
            RecurringScanProgressModal,
        )

        try:
            repo.clear_scan(
                section_id=session.section_id,
                range_start=range_start, range_end=range_end,
            )
        except Exception:  # pragma: no cover
            log.exception("recurring clear_scan failed (계속 진행)")

        progress = RecurringScanProgressModal(initial="📊 거래 fetch 시작…")
        self.app.push_screen(progress)  # type: ignore[attr-defined]
        await asyncio.sleep(0)

        running_total = [0]
        chunk_count = [0]

        def _on_fetch_progress(kind: str, start: str, end: str, **extra: Any) -> None:
            try:
                if kind == "fetch":
                    chunk_count[0] += 1
                    progress.set_activity(
                        f"📡 후잉 요청 #{chunk_count[0]} → /entries.json\n"
                        f"{start} ~ {end}\n"
                        f"받은 거래 누적 {running_total[0]:,} 건",
                    )
                elif kind == "received":
                    running_total[0] += int(extra.get("count") or 0)
                    progress.set_activity(
                        f"📥 받음 {extra.get('count') or 0} 건\n{start} ~ {end}\n"
                        f"받은 거래 누적 {running_total[0]:,} 건",
                    )
                elif kind == "done":
                    progress.set_activity(
                        f"✅ fetch 완료 — 총 {extra.get('total') or 0:,} 건 수신",
                    )
            except Exception:  # pragma: no cover
                log.exception("recurring fetch progress callback failed")

        try:
            self.set_status(
                f"⏳ 반복 누락 검사 — 거래 fetch 중… ({range_start} ~ {range_end})",
            )
            await asyncio.sleep(0)
            try:
                entries = await self._client.list_entries(
                    section_id=session.section_id,
                    start_date=range_start, end_date=range_end,
                    on_progress=_on_fetch_progress,
                )
            except ToolError as e:
                self.set_status(
                    f"거래 조회 실패 [{e.kind}] {e.message}", error=True,
                )
                return []
            except Exception as e:
                log.exception("scan_recurring fetch failed")
                self.set_status(f"거래 조회 실패 (INTERNAL): {e}", error=True)
                return []
            progress.set_activity(
                f"🔁 반복 시리즈 분석 중…\n"
                f"거래 {len(entries):,} 건 — 주기 추정 + 누락 투영\n"
                f"(계정·item 그룹 → 간격 중앙값 → 기대 날짜 매칭)",
            )
            await asyncio.sleep(0)
            series = detect_recurring_omissions(entries, as_of=range_end)
            if not series:
                self.set_status(
                    f"✅ 거래 {len(entries):,} 건 검사 완료 — 누락 의심 없음.",
                )
                return []
            progress.set_activity(
                f"💾 결과 저장 중…\n{len(series)} 개 시리즈 sqlite 영구화\n"
                f"(테이블: recurring_scan_series · status=pending)",
            )
            await asyncio.sleep(0)
            stored = repo.save_scan(
                section_id=session.section_id,
                range_start=range_start, range_end=range_end,
                series=series,
            )
            self.set_status(
                f"🔁 누락 의심 반복 시리즈 {len(stored)} 개 발견 — 검토 시작.",
            )
            return stored
        finally:
            try:
                progress.dismiss()
            except Exception:  # pragma: no cover
                pass

