# scripts/utils/excel_writer.py
# Excel 写入器：从 intermediate.json + s1_decisions.json + s2_decisions.json [+ s3_hazop.json] 生成 HARA Excel
# S3 阶段可选：传入 s3_hazop.json 则同时写入 HAZOP 分析表
# 支持：模板文件 / 模板 bytes / 无模板（自动生成简版）

import io
import json
import os
import tempfile
from copy import copy
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, Alignment, Border, Side

from utils.data_models import FAILURE_MODES
from utils.output_reconciliation import require_reconciled_workbook


class ExcelWriter:
    """从 JSON 决策文件读取结果并写入 Excel"""

    def __init__(self, template_path: str = None, template_bytes: bytes = None):
        self.template_path = template_path
        self.template_bytes = template_bytes

    def write(self, intermediate_path: str, s1_decisions_path: str,
              s2_decisions_path: str, output_path: str,
              s3_hazop_path: str = None,
              s4_hara_path: str = None,
              s5_safety_goals_path: str = None) -> None:
        """从 JSON 决策文件生成最终 Excel。

        s3_hazop_path: 可选，HAZOP 分析结果 JSON，传入则写入 HAZOP 分析表
        s4_hara_path: 可选，HARA 分析结果 JSON（validate 后的 final），
                      传入则写入 HARA 分析表并回填 HAZOP G 列
        s5_safety_goals_path: 可选，整车安全目标汇总 JSON，
                      传入则写入"整车安全目标"sheet
        """
        def _load(path, label):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except FileNotFoundError:
                print(f"错误: {label} 文件不存在: {path}")
                raise
            except json.JSONDecodeError as e:
                print(f"错误: {label} JSON 格式无效: {e}")
                raise

        intermediate = _load(intermediate_path, "intermediate")
        s1 = _load(s1_decisions_path, "s1_decisions")
        s2 = _load(s2_decisions_path, "s2_decisions")

        s3 = None
        if s3_hazop_path:
            s3 = _load(s3_hazop_path, "s3_hazop")

        s4 = None
        if s4_hara_path:
            s4 = _load(s4_hara_path, "s4_hara")

        s5 = None
        if s5_safety_goals_path:
            s5 = _load(s5_safety_goals_path, "s5_safety_goals")

        wb = self._load_workbook()

        self._write_related_items(wb, intermediate, s1)
        self._write_failure_modes(wb, s2)
        if s3:
            self._write_hazop(wb, s3, s4)
        if s4:
            self._write_hara(wb, s4)
        if s5:
            self._write_safety_goals(wb, s4, s5)

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        # 先写入同目录临时文件并执行全阶段语义对账。只有对账通过后才原子替换
        # 正式输出，避免业务列漏写时仍留下一个看似成功的 Excel。
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{out.stem}.", suffix=out.suffix or ".xlsx", dir=str(out.parent)
        )
        os.close(fd)
        temp_out = Path(temp_name)
        try:
            wb.save(str(temp_out))
            require_reconciled_workbook(
                temp_out,
                intermediate=intermediate,
                s1=s1,
                s2=s2,
                s3=s3,
                s4=s4,
                s5=s5,
            )
            os.replace(temp_out, out)
        finally:
            if temp_out.exists():
                temp_out.unlink()
        print(f"[Writer] 已保存: {out}")

    # ========== 内部 ==========

    def _load_workbook(self):
        if self.template_path:
            return openpyxl.load_workbook(str(self.template_path))
        if self.template_bytes:
            return openpyxl.load_workbook(io.BytesIO(self.template_bytes))
        return self._create_minimal_workbook()

    def _find_sheet(self, wb, keywords):
        for name in wb.sheetnames:
            for kw in keywords:
                if kw in name:
                    return wb[name]
        return None

    # ========== 相关项功能清单 ==========

    def _write_related_items(self, wb, intermediate: dict, s1: dict):
        ws = self._find_sheet(wb, ["相关项功能清单", "相关项功能列表", "相关项"])
        if ws is None:
            print("[Writer] 警告: 未找到相关项功能清单/列表 sheet，跳过")
            return

        # 相关项定义文档列必须使用输入文档的原始文件名；
        # source_document.original_name 是接入阶段记录的权威值，
        # doc_name 仅作为兼容旧 intermediate 的回退。
        source_document = intermediate.get("source_document") or {}
        doc_name = source_document.get("original_name") or intermediate.get("doc_name", "")

        # 解除数据区合并
        for merged in list(ws.merged_cells.ranges):
            if merged.min_row >= 2:
                ws.unmerge_cells(str(merged))

        # 清空旧数据
        for r in range(2, ws.max_row + 1):
            for c in range(1, 9):
                ws.cell(row=r, column=c).value = None

        # 构建 S1 索引
        s1_index = {}
        for d in s1.get("decisions", []):
            s1_index[d["func_id"]] = {sf["feature_list_id"]: sf for sf in d.get("sub_functions", [])}

        row = 2
        for item in intermediate.get("related_items", []):
            item_start = row
            func_id = item["func_id"]
            func_name = item["func_name"]

            # 按 feature_list_id 分组（同一 ID 的多行合并）
            id_groups = []
            id_map = {}
            for sub in item.get("sub_functions", []):
                fid = sub["feature_list_id"]
                if fid not in id_map:
                    id_map[fid] = (sub, [])
                    id_groups.append((fid, sub, id_map[fid][1]))
                id_map[fid][1].append(row)

                s1_sub = s1_index.get(func_id, {}).get(fid, {})
                is_hara = s1_sub.get("is_hara", True)
                remark = s1_sub.get("remark", "/")
                # Domain Pack/权威表说明属于内部追溯元数据，不写入用户可见的
                # “相关项功能清单”备注列；保留 s1_decisions.json 中的原字段。
                if s1_sub.get("source") in {"pt_subfunction_authority", "domain_subfunction_authority", "domain_pack"}:
                    remark = "/"

                ws.cell(row=row, column=1, value=func_id)
                ws.cell(row=row, column=2, value=func_name)
                ws.cell(row=row, column=3, value=fid)
                # 功能描述：优先用 description，没有则用 name 回落（docx表中描述文本常在name列）
                desc = sub.get("description")
                if not desc:
                    desc = sub.get("name")
                ws.cell(row=row, column=4, value=desc or None)
                ws.cell(row=row, column=5, value="是" if is_hara else "否")
                ws.cell(row=row, column=6, value=doc_name)
                ws.cell(row=row, column=7, value=sub.get("chapter") or None)
                ws.cell(row=row, column=8, value=remark if remark != "/" else None)
                row += 1

            item_end = row - 1

            # 合并相关项级列
            if item_end > item_start:
                for col in (1, 2):
                    ws.merge_cells(start_row=item_start, start_column=col,
                                   end_row=item_end, end_column=col)

            # 合并子功能 ID 级列
            for fid, first_sub, rows in id_groups:
                if len(rows) > 1:
                    for col in (3, 4, 5, 8):
                        ws.merge_cells(start_row=rows[0], start_column=col,
                                       end_row=rows[-1], end_column=col)

        print(f"[Writer] 相关项功能清单: 写入 {row - 2} 行")

    # ========== 失效模式 ==========

    def _write_failure_modes(self, wb, s2: dict):
        ws = self._find_sheet(wb, ["失效模式"])
        if ws is None:
            print("[Writer] 警告: 未找到失效模式 sheet，跳过")
            return

        decisions = s2.get("decisions", [])
        if not decisions:
            print("[Writer] 警告: s2_decisions.json 为空，跳过失效模式写入")
            return

        self._ensure_all_modes(ws)

        # 构建列索引
        mode_cols = {}
        reason_col = None
        for col in range(1, ws.max_column + 1):
            header = ws.cell(row=1, column=col).value
            if header:
                h = str(header).strip()
                if h in FAILURE_MODES:
                    mode_cols[h] = col
                if "选择理由" in h:
                    reason_col = col

        n = len(decisions)

        # 解除合并 + 插入行
        data_merges = []
        for merged in list(ws.merged_cells.ranges):
            if merged.min_row >= 2:
                data_merges.append(str(merged))
                ws.unmerge_cells(str(merged))

        if n > 1:
            ws.insert_rows(3, amount=n - 1)

        note_row = 2 + max(n, 1)
        for r in range(2, note_row):
            for c in range(1, ws.max_column + 1):
                ws.cell(row=r, column=c).value = None

        if data_merges:
            ws.merge_cells(start_row=note_row, start_column=1,
                           end_row=note_row, end_column=ws.max_column)

        for i, d in enumerate(decisions):
            r = 2 + i
            ws.cell(row=r, column=1, value=d.get("func_id", ""))
            ws.cell(row=r, column=2, value=d.get("func_name", ""))
            for mode in d.get("selected_modes", []):
                if mode in mode_cols:
                    ws.cell(row=r, column=mode_cols[mode], value="√")
            if reason_col:
                # Domain Pack 的内部路由说明用于审计/合同校验，不写入
                # “失效模式”Sheet 的用户可见“选择理由”列。
                reason = "" if d.get("source") == "domain_pack" else d.get("reason", "")
                ws.cell(row=r, column=reason_col, value=reason)

        print(f"[Writer] 失效模式: 写入 {n} 行, {len(mode_cols)} 种模式列")

    # ========== HAZOP 分析 ==========

    @staticmethod
    def _hazop_associated_hara_value(hazard_description, s4_id_range, raw_associated_hara):
        """Return the controlled HAZOP association value for Excel column G.

        A HAZOP conclusion of no vehicle-level hazard is never a HARA event.
        S3 validation rejects contradictory input; this writer guard prevents a
        contradictory value from reaching the published workbook as well.

        2026-08-26 修复: 过滤掉"是/否"等非ID值，只写入危害事件ID或"不涉及"
        """
        text = hazard_description.strip().lower() if isinstance(hazard_description, str) else ""
        if any(token in text for token in ("无整车层面危害", "无整车危害", "不涉及", "无危害", "n/a")):
            return "不涉及"

        # 优先使用S4回填的ID范围
        if s4_id_range:
            return s4_id_range

        # 过滤掉"是/否"等布尔值，只接受ID格式或空值
        if raw_associated_hara and isinstance(raw_associated_hara, str):
            raw = raw_associated_hara.strip()
            # 如果是"是/否/有/无"等非ID值，返回None（表示待S4回填）
            if raw in ("是", "否", "有", "无", "yes", "no", "Y", "N"):
                return None
            # 如果看起来像ID（包含字母数字下划线），返回原值
            if any(c.isalnum() or c in ('_', '-') for c in raw):
                return raw

        return None

    def _write_hazop(self, wb, s3: dict, s4: dict = None):
        """从 s3_hazop.json 写入 HAZOP 分析表。模板 Row 1（"HAZOP分析"标题）和 Row 2（列标题）完全不动，仅从 Row 3 开始写数据。

        s4: 可选，validate 后的 s4_hara_final.json，传入则回填 G 列"关联HARA"的 ID 范围。

        JSON 格式（支持一个异常对应多个危害）：
        {
          "entries": [
            {
              "func_id": "CB_func_0002", "func_name": "电子驻车制动",
              "failure_mode": "非预期",
              "anomalies": [
                {
                  "description": "非预期接合驻车制动器",
                  "failure_id": "CB_MF_0002_03",
                  "hazards": [
                    {"description": "非预期车辆横向运动", "associated_hara": ""},
                    {"description": "车辆非预期纵向减速", "associated_hara": ""}
                  ]
                }
              ]
            }
          ]
        }
        兼容旧格式：anomaly 中 hazard 为字符串时视为单个危害。
        """
        ws = self._find_sheet(wb, ["HAZOP 分析", "HAZOP分析", "HAZOP"])
        if ws is None:
            ws = self._create_hazop_sheet(wb)

        entries = s3.get("entries", [])
        if not entries:
            print("[Writer] 警告: s3_hazop.json 为空，跳过 HAZOP 分析写入")
            return

        # 从 s4 构建 hazard 级索引，用于精确回填 G 列。
        # 保留 failure_id 级索引兼容旧 S4，但 PT 新合同优先使用 hazard_key。
        s4_id_ranges = {}
        s4_hazard_ranges = {}
        if s4:
            for hz in s4.get("hazards", []):
                fid = hz.get("failure_id", "")
                id_range = hz.get("id_range")
                hkey = hz.get("hazard_key")
                if hkey and id_range:
                    s4_hazard_ranges[hkey] = id_range
                if fid and id_range:
                    if fid not in s4_id_ranges:
                        s4_id_ranges[fid] = id_range
                    else:
                        # 旧格式的 failure 级合并，兼容历史输出；新 PT 输出走 hazard_key。
                        old_start = s4_id_ranges[fid].replace("~", "-").split("-")[0]
                        new_end = id_range.replace("~", "-").split("-")[-1]
                        s4_id_ranges[fid] = f"{old_start}-{new_end}"

        # 仅解除数据区（Row 3 及以下）的合并，保留 Row 1 标题和 Row 2 列标题不动
        for merged in list(ws.merged_cells.ranges):
            if merged.min_row >= 3:
                ws.unmerge_cells(str(merged))

        # 清除旧数据（Row 3 起，保留 Row 1-2 表头不动）
        for r in range(3, ws.max_row + 1):
            for c in range(1, ws.max_column + 1):
                ws.cell(row=r, column=c).value = None

        # 写入数据（从 Row 3 开始）
        row = 3
        total_rows = 0
        # 跟踪每个 func_id 的起始行和结束行，用于跨 entry 合并
        func_ranges = {}  # {func_id: [start_row, end_row]}

        for entry in entries:
            func_id = entry.get("func_id", "")
            func_name = entry.get("func_name", "")
            failure_mode = entry.get("failure_mode", "")
            anomalies = entry.get("anomalies", [])

            entry_start = row
            # 记录 func_id 范围
            if func_id not in func_ranges:
                func_ranges[func_id] = [row, row]
            else:
                func_ranges[func_id][1] = row

            # 先写功能 ID、功能名、失效模式（首行）
            ws.cell(row=row, column=1, value=func_id)
            ws.cell(row=row, column=2, value=func_name)
            ws.cell(row=row, column=3, value=failure_mode)

            for anom in anomalies:
                anom_desc = anom.get("description", "")
                failure_id = anom.get("failure_id", "")

                # 支持 hazards 列表（一个异常多个危害）和旧格式 hazard 字符串
                hazards = anom.get("hazards")
                if hazards is None:
                    h = anom.get("hazard", "")
                    ah = anom.get("associated_hara", "") or None
                    hazards = [{"description": h, "associated_hara": ah}]

                anom_start = row
                for j, hz in enumerate(hazards):
                    if j == 0:
                        ws.cell(row=row, column=4, value=anom_desc)
                        ws.cell(row=row, column=5, value=failure_id)
                    ws.cell(row=row, column=6, value=hz.get("description", ""))
                    # G 列：优先用 s4 回填的 ID 范围，否则用 s3 原始值
                    hazard_key = f"{failure_id}__{j + 1}"
                    s4_range = s4_hazard_ranges.get(hazard_key) or s4_id_ranges.get(failure_id)
                    g_val = self._hazop_associated_hara_value(
                        hz.get("description", ""),
                        s4_range,
                        hz.get("associated_hara", ""),
                    )
                    ws.cell(row=row, column=7, value=g_val)
                    row += 1
                    total_rows += 1

                # 合并：异常描述、功能失效ID（同一异常多个危害时）
                anom_end = row - 1
                if anom_end > anom_start:
                    for col in (4, 5):
                        ws.merge_cells(start_row=anom_start, start_column=col,
                                       end_row=anom_end, end_column=col)

            entry_end = row - 1
            func_ranges[func_id][1] = entry_end

            # 合并：失效模式（如有多行）
            if entry_end > entry_start:
                ws.merge_cells(start_row=entry_start, start_column=3,
                               end_row=entry_end, end_column=3)

        # 跨 entry 合并：功能 ID、功能名（同一 func_id 的所有行）
        for func_id, (start, end) in func_ranges.items():
            if end > start:
                for col in (1, 2):
                    ws.merge_cells(start_row=start, start_column=col,
                                   end_row=end, end_column=col)

        print(f"[Writer] HAZOP 分析: 写入 {len(entries)} 个功能-模式组合, "
              f"{total_rows} 行数据")

    # ========== HARA 分析 ==========

    def _write_hara(self, wb, s4: dict):
        """从 s4_hara_final.json 写入 HARA 分析表。

        19 列 A-S：
        A=危害事件ID, B=整车功能, C=功能失效ID, D=功能异常表现, E=整车危害,
        F=运行场景, G=危害事件描述, H=S, I=S理由, J=E, K=E理由,
        L=C, M=C理由, N=ASIL, O=安全目标ID, P=安全目标, Q=安全状态, R=FTTI, S=注释

        每个功能前有一个分组标题行（合并 A:E），格式：{func_id}：{func_name}
        skip=true 不生成行；is_reference=true 生成引用行（F 列写引用文本）。
        """
        ws = self._find_sheet(wb, ["HARA 分析", "HARA分析", "HARA"])
        if ws is None:
            ws = self._create_hara_sheet(wb)

        hazards = s4.get("hazards", [])
        if not hazards:
            print("[Writer] 警告: s4_hara.json 无 hazards，跳过 HARA 分析写入")
            return

        # 解除数据区（Row 3 及以下）的合并，保留 Row 1-2 表头
        for merged in list(ws.merged_cells.ranges):
            if merged.min_row >= 3:
                ws.unmerge_cells(str(merged))

        # 清理 Row 3 及以下的残留数据
        for r in range(3, ws.max_row + 1):
            for c in range(1, 20):
                ws.cell(row=r, column=c).value = None

        # 如果模板超过 19 列（如新项目 21 列），删除多余列
        if ws.max_column > 19:
            ws.delete_cols(20, ws.max_column - 19)

        # 样式定义
        thin_border = Border(
            left=Side(style="thin"), right=Side(style="thin"),
            top=Side(style="thin"), bottom=Side(style="thin"),
        )
        group_font = Font(name="微软雅黑", bold=True, size=11)
        group_fill = openpyxl.styles.PatternFill(
            start_color="D9E1F2", end_color="D9E1F2", fill_type="solid"
        )
        center_wrap = Alignment(horizontal="center", vertical="center", wrap_text=True)
        left_wrap = Alignment(horizontal="left", vertical="center", wrap_text=True)
        center = Alignment(horizontal="center", vertical="center")

        # 按 func_id 分组（保持出现顺序）
        func_order = []
        func_hazards = {}
        for hz in hazards:
            fid = hz.get("func_id", "")
            if fid not in func_hazards:
                func_hazards[fid] = []
                func_order.append(fid)
            func_hazards[fid].append(hz)

        # 列宽设置
        col_widths = {
            "A": 18, "B": 14, "C": 16, "D": 16, "E": 28,
            "F": 28, "G": 40, "H": 6, "I": 30, "J": 6,
            "K": 30, "L": 6, "M": 30, "N": 8, "O": 16,
            "P": 24, "Q": 24, "R": 10, "S": 20,
        }
        for col_letter, width in col_widths.items():
            ws.column_dimensions[col_letter].width = width

        row = 3
        total_events = 0

        for func_id in func_order:
            group = func_hazards[func_id]
            func_name = group[0].get("func_name", "")

            # 分组标题行：合并 A:E
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=5)
            group_cell = ws.cell(row=row, column=1, value=f"{func_id}：{func_name}")
            group_cell.font = group_font
            group_cell.fill = group_fill
            group_cell.alignment = center
            for c in range(1, 6):
                ws.cell(row=row, column=c).border = thin_border
                ws.cell(row=row, column=c).fill = group_fill
            row += 1

            for hz in group:
                # skip=true 不生成行
                if hz.get("skip"):
                    continue

                failure_id = hz.get("failure_id", "")
                anomaly = hz.get("anomaly", "")
                # S4 正式合同使用 ``hazard`` 保存整车危害。``vehicle_hazard``
                # 仅用于兼容早期产物；不能优先读取旧字段，否则当前 S4 的 E 列
                # 会被整列写空。
                hazard_desc = hz.get("hazard") or hz.get("vehicle_hazard") or ""

                # 正常事件行
                events = hz.get("events", [])
                for ev in events:
                    asil = ev.get("asil")
                    # ASIL≥A 时才写安全目标/安全状态/FTTI；QM/ASIL 留空（包括 S=0）
                    has_sg = asil in ("A", "B", "C", "D")
                    ws.cell(row=row, column=1, value=ev.get("hazard_id"))
                    ws.cell(row=row, column=2, value=func_name)
                    ws.cell(row=row, column=3, value=failure_id)
                    ws.cell(row=row, column=4, value=anomaly)
                    ws.cell(row=row, column=5, value=hazard_desc)
                    ws.cell(row=row, column=6, value=ev.get("scenario"))
                    ws.cell(row=row, column=7, value=ev.get("description"))
                    ws.cell(row=row, column=8, value=ev.get("severity"))
                    ws.cell(row=row, column=9, value=ev.get("severity_reason"))
                    ws.cell(row=row, column=10, value=ev.get("exposure"))
                    ws.cell(row=row, column=11, value=ev.get("exposure_reason"))
                    ws.cell(row=row, column=12, value=ev.get("controllability"))
                    ws.cell(row=row, column=13, value=ev.get("controllability_reason"))
                    ws.cell(row=row, column=14, value=asil)
                    ws.cell(row=row, column=15, value=ev.get("safety_goal_id") if has_sg else None)
                    ws.cell(row=row, column=16, value=ev.get("safety_goal") if has_sg else None)
                    ws.cell(row=row, column=17, value=ev.get("safe_state") if has_sg else None)
                    ws.cell(row=row, column=18, value=ev.get("ftti") if has_sg else None)
                    # S列不导出内部 note；追溯信息保留在 S4 JSON。
                    ws.cell(row=row, column=19, value=None)
                    total_events += 1
                    row += 1

        # 为所有数据行设置边框和对齐
        for r in range(3, row):
            for c in range(1, 20):
                cell = ws.cell(row=r, column=c)
                cell.border = thin_border
                # H/J/L/N 列（S/E/C/ASIL）居中，其余左对齐自动换行
                if c in (8, 10, 12, 14):
                    cell.alignment = center
                elif c in (1, 3, 15, 18):
                    cell.alignment = center_wrap
                else:
                    cell.alignment = left_wrap

        print(f"[Writer] HARA 分析: {len(func_order)} 个功能, "
              f"{total_events} 个危害事件")

    # ========== 整车安全目标 ==========

    def _write_safety_goals(self, wb, s4: dict, s5: dict):
        """从 s4_hara_final.json + safety_goals.json 写入"整车安全目标"sheet。

        模板双区结构（Row4 表头，数据从 Row5 开始）：
          左区 A-E：事件级明细（每个非QM有SG的危害事件一行）
            A=安全目标ID(事件级), B=安全目标, C=ASIL, D=安全状态, E=FTTI
          F 列"序号"：每个 SG 组的序号（1,2,3…），组内合并
          右区 G-L：整车级汇总（每个唯一SG只在首行填写）
            G=整车安全目标ID, H=安全目标合并, I=ASIL, J=Safe State, K=FTTI, L=备注
          F-K 列按 SG 组纵向合并（与参考 Excel 格式一致）。

        事件按 s5 的 SG 顺序分组排列（ASIL降序，同ASIL按HARA表首次出现顺序），
        同一 SG 的事件连续排列，整车级汇总写在该组首行。
        """
        ws = self._find_sheet(wb, ["整车安全目标", "安全目标", "Safety Goal"])
        if ws is None:
            ws = self._create_safety_goal_sheet(wb)

        sgs = s5.get("safety_goals", [])
        if not sgs:
            print("[Writer] 警告: safety_goals.json 无数据，跳过整车安全目标写入")
            return

        # ---- 从 S5 显式映射建立事件级 SG → 整车级 SG 关系 ----
        # 不能按 SG 文本反查：同一文本在不同危害族/ASIL/安全状态/FTTI 下可能属于不同组。
        mapping_rows = s5.get("event_safety_goal_mappings", [])
        event_to_vehicle_sg = {}
        if isinstance(mapping_rows, list):
            for mapping in mapping_rows:
                if not isinstance(mapping, dict):
                    continue
                event_sg_id = mapping.get("event_safety_goal_id")
                vehicle_sg_id = mapping.get("vehicle_safety_goal_id")
                if isinstance(event_sg_id, str) and event_sg_id and isinstance(vehicle_sg_id, str) and vehicle_sg_id:
                    if event_sg_id in event_to_vehicle_sg and event_to_vehicle_sg[event_sg_id] != vehicle_sg_id:
                        raise ValueError(f"S5 映射冲突：事件级安全目标 {event_sg_id} 指向多个整车级 SG")
                    event_to_vehicle_sg[event_sg_id] = vehicle_sg_id

        sg_info_map = {sg.get("sg_id"): sg for sg in sgs if isinstance(sg, dict) and sg.get("sg_id")}
        # 兼容历史 S5：只有当同文本恰好对应一个整车级 SG 时才允许回退。
        legacy_text_map = {}
        for sg_id, info in sg_info_map.items():
            text_key = info.get("safety_goal")
            if isinstance(text_key, str) and text_key:
                legacy_text_map.setdefault(text_key, []).append(sg_id)

        # ---- 从 s4 提取所有 ASIL≥A 且有SG的事件 ----
        event_rows = []
        for hazard in s4.get("hazards", []):
            if hazard.get("skip"):
                continue
            for ev in hazard.get("events", []):
                sg_text = ev.get("safety_goal")
                asil = ev.get("asil")
                event_sg_id = ev.get("safety_goal_id") or ""
                if not sg_text or sg_text == "None" or not asil or asil in ("-", "QM"):
                    continue
                vehicle_sg_id = event_to_vehicle_sg.get(event_sg_id)
                if not vehicle_sg_id:
                    candidates = legacy_text_map.get(sg_text.strip(), [])
                    if len(candidates) == 1:
                        vehicle_sg_id = candidates[0]
                    else:
                        raise ValueError(
                            f"S5 缺少事件级安全目标 {event_sg_id or '<missing>'} 的整车级映射；"
                            "不能按模糊 SG 文本写入 Excel"
                        )
                event_rows.append({
                    "vehicle_sg_id": vehicle_sg_id,
                    "safety_goal_id": event_sg_id,
                    "safety_goal": sg_text.strip(),
                    "asil": asil,
                    "safe_state": ev.get("safe_state") or "",
                    "ftti": ev.get("ftti") or "",
                })

        if not event_rows:
            print("[Writer] 警告: s4 中无非QM安全目标事件，跳过整车安全目标写入")
            return

        # ---- 按 s5 的 SG 顺序分组事件 ----
        sg_order = {sg.get("sg_id"): idx for idx, sg in enumerate(sgs) if isinstance(sg, dict)}
        event_rows.sort(key=lambda e: (sg_order.get(e["vehicle_sg_id"], 999), e["safety_goal_id"]))

        # ---- 清理 Row5 及以下残留数据（保留 Row1-3 标题合并、Row4 表头不动）----
        # 模板的历史事件明细通常已纵向合并；必须先解除数据区合并，
        # 否则 MergedCell 不可写会中断整本工作簿的输出。
        for merged in list(ws.merged_cells.ranges):
            if merged.max_row >= 5:
                ws.unmerge_cells(str(merged))
        for r in range(5, ws.max_row + 1):
            for c in range(1, 13):
                ws.cell(row=r, column=c).value = None

        # ---- 样式 ----
        thin_border = Border(
            left=Side(style="thin"), right=Side(style="thin"),
            top=Side(style="thin"), bottom=Side(style="thin"),
        )
        center = Alignment(horizontal="center", vertical="center", wrap_text=True)
        left_wrap = Alignment(horizontal="left", vertical="center", wrap_text=True)

        # ---- 写入数据 ----
        row = 5
        written_sgs = set()  # 已写过整车级汇总的 vehicle_sg_id
        total_events = 0
        # 记录每个 SG 组的起止行，用于后续合并: [(vehicle_sg_id, start_row, end_row, seq), ...]
        group_ranges = []
        current_group_start = None
        current_sg = None
        group_seq = 0

        for ev in event_rows:
            vehicle_sg_id = ev["vehicle_sg_id"]
            is_first_of_group = vehicle_sg_id not in written_sgs

            # 新组开始：记录上一组的结束行
            if is_first_of_group:
                if current_sg is not None:
                    group_ranges.append((current_sg, current_group_start, row - 1, group_seq))
                current_group_start = row
                current_sg = vehicle_sg_id
                group_seq += 1

            # 左区 A-E：事件级明细（每行都写）
            ws.cell(row=row, column=1, value=ev["safety_goal_id"])
            ws.cell(row=row, column=2, value=ev["safety_goal"])
            ws.cell(row=row, column=3, value=ev["asil"])
            ws.cell(row=row, column=4, value=ev["safe_state"])
            ws.cell(row=row, column=5, value=ev["ftti"])

            # F 列：序号（仅每个 SG 组首行写，后续合并）
            if is_first_of_group:
                ws.cell(row=row, column=6, value=group_seq)

            # 右区 G-L：整车级汇总（仅每个SG组首行写）
            if is_first_of_group and vehicle_sg_id in sg_info_map:
                info = sg_info_map[vehicle_sg_id]
                ws.cell(row=row, column=7, value=info.get("sg_id", ""))
                ws.cell(row=row, column=8, value=info.get("safety_goal", ""))
                ws.cell(row=row, column=9, value=info.get("asil", ""))
                ws.cell(row=row, column=10, value=info.get("safe_state") or "")
                ws.cell(row=row, column=11, value=info.get("ftti") or "")
                ws.cell(row=row, column=12, value="")  # 备注列留空
                written_sgs.add(vehicle_sg_id)

            # 样式
            for c in range(1, 13):
                cell = ws.cell(row=row, column=c)
                cell.border = thin_border
                if c in (1, 3, 5, 6, 7, 9, 11):
                    cell.alignment = center
                else:
                    cell.alignment = left_wrap

            row += 1
            total_events += 1

        # 记录最后一组的结束行
        if current_sg is not None:
            group_ranges.append((current_sg, current_group_start, row - 1, group_seq))

        # ---- 合并 F-K 列（每个 SG 组逐列纵向合并，L 备注列不合并）----
        for _sg_text, start_r, end_r, _seq in group_ranges:
            if end_r > start_r:
                for col in range(6, 12):  # F=6 到 K=11
                    ws.merge_cells(start_row=start_r, start_column=col,
                                   end_row=end_r, end_column=col)

        # 列宽（与模板协调）
        col_widths = {
            "A": 16, "B": 45, "C": 8, "D": 24, "E": 12,
            "F": 6, "G": 16, "H": 45, "I": 8, "J": 24, "K": 12, "L": 15,
        }
        for col_letter, width in col_widths.items():
            ws.column_dimensions[col_letter].width = width

        print(f"[Writer] 整车安全目标: {len(sgs)} 个整车级SG, {total_events} 个事件级明细")

    @staticmethod
    def _create_safety_goal_sheet(wb):
        """在无模板时创建可读、可对账的整车安全目标 Sheet。"""
        ws = wb.create_sheet("整车安全目标")
        header_font = Font(name="微软雅黑", bold=True, size=10)
        thin_border = Border(
            left=Side(style="thin"), right=Side(style="thin"),
            top=Side(style="thin"), bottom=Side(style="thin"),
        )
        center = Alignment(horizontal="center", vertical="center", wrap_text=True)
        headers = {
            1: "安全目标 ID", 2: "安全目标", 3: "ASIL", 4: "安全状态", 5: "FTTI",
            6: "序号", 7: "整车安全目标 ID", 8: "整车安全目标", 9: "ASIL",
            10: "安全状态", 11: "FTTI", 12: "备注",
        }
        for column, value in headers.items():
            cell = ws.cell(row=4, column=column, value=value)
            cell.font = header_font
            cell.border = thin_border
            cell.alignment = center
        return ws

    @staticmethod
    def _create_hara_sheet(wb):
        """在无模板时自动创建 HARA 分析 Sheet（2 行表头，与模板格式一致）"""
        ws = wb.create_sheet("HARA 分析")
        title_font = Font(name="微软雅黑", bold=True, size=11)
        header_font = Font(name="微软雅黑", bold=True, size=10)
        thin_border = Border(
            left=Side(style="thin"), right=Side(style="thin"),
            top=Side(style="thin"), bottom=Side(style="thin"),
        )
        center_wrap = Alignment(horizontal="center", vertical="center", wrap_text=True)

        # Row 1: 分组表头
        group_headers = [
            (1, "危害事件ID"), (2, "整车功能"), (3, "功能失效ID"),
            (4, "危害识别"), (8, "风险评估"), (15, "定义安全目标"),
        ]
        for col, text in group_headers:
            cell = ws.cell(row=1, column=col, value=text)
            cell.font = title_font
            cell.alignment = center_wrap
            cell.border = thin_border

        # Row 1 合并
        ws.merge_cells("A1:A2")
        ws.merge_cells("B1:B2")
        ws.merge_cells("C1:C2")
        ws.merge_cells("D1:E1")
        ws.merge_cells("F1:G1")
        ws.merge_cells("H1:N1")
        ws.merge_cells("O1:S1")

        # Row 2: 列标题
        col_headers = {
            4: "功能异常表现", 5: "整车危害", 6: "运行场景\n(驾驶和运行情况)",
            7: "危害事件描述", 8: "严重度\n(S)", 9: '对"S"的理由',
            10: "暴露概率\n(E)", 11: '对"E"的理由', 12: "可控性（C）",
            13: '对"C"的理由', 14: "ASIL", 15: "安全目标 ID",
            16: "安全目标", 17: "安全状态", 18: "FTTI", 19: "注释",
        }
        for col, text in col_headers.items():
            cell = ws.cell(row=2, column=col, value=text)
            cell.font = header_font
            cell.alignment = center_wrap
            cell.border = thin_border

        # 列宽
        col_widths = {
            "A": 18, "B": 14, "C": 16, "D": 16, "E": 28,
            "F": 28, "G": 40, "H": 6, "I": 30, "J": 6,
            "K": 30, "L": 6, "M": 30, "N": 8, "O": 16,
            "P": 24, "Q": 24, "R": 10, "S": 20,
        }
        for col_letter, width in col_widths.items():
            ws.column_dimensions[col_letter].width = width

        return ws

    @staticmethod
    def _create_hazop_sheet(wb):
        """在无模板时自动创建 HAZOP 分析 Sheet（Row 1 大标题 + Row 2 列标题，与模板格式一致）"""
        ws = wb.create_sheet("HAZOP 分析")
        title_font = Font(name="微软雅黑", bold=True, size=12)
        header_font = Font(name="微软雅黑", bold=True, size=11)
        thin_border = Border(
            left=Side(style="thin"), right=Side(style="thin"),
            top=Side(style="thin"), bottom=Side(style="thin"),
        )
        # Row 1: 大标题（合并 A1:G1）
        ws.merge_cells("A1:G1")
        title_cell = ws.cell(row=1, column=1, value="HAZOP分析")
        title_cell.font = title_font
        title_cell.alignment = Alignment(horizontal="center", vertical="center")

        # Row 2: 列标题
        headers = ["整车功能 ID", "整车功能", "功能失效模式",
                   "功能异常表现", "功能失效 ID", "整车危害", "关联 HARA"]
        widths = [18, 22, 12, 40, 18, 30, 30]
        for c, (h, w) in enumerate(zip(headers, widths), 1):
            cell = ws.cell(row=2, column=c, value=h)
            cell.font = header_font
            cell.border = thin_border
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            ws.column_dimensions[openpyxl.utils.get_column_letter(c)].width = w
        return ws

    def _ensure_all_modes(self, ws):
        existing = set()
        reason_col = None
        for col in range(1, ws.max_column + 1):
            header = ws.cell(row=1, column=col).value
            if header:
                h = str(header).strip()
                if h in FAILURE_MODES:
                    existing.add(h)
                if "选择理由" in h:
                    reason_col = col

        missing = [m for m in FAILURE_MODES if m not in existing]
        if not missing:
            return

        insert_at = reason_col if reason_col else ws.max_column + 1
        for i, mode in enumerate(missing):
            col_idx = insert_at + i
            ws.insert_cols(col_idx)
            ws.cell(row=1, column=col_idx, value=mode)
            ref_col = col_idx - 1
            for r in range(1, ws.max_row + 1):
                src = ws.cell(row=r, column=ref_col)
                dst = ws.cell(row=r, column=col_idx)
                if src.has_style:
                    dst.font = copy(src.font)
                    dst.fill = copy(src.fill)
                    dst.border = copy(src.border)
                    dst.alignment = copy(src.alignment)
                    dst.number_format = src.number_format
            print(f"[Writer] 失效模式补列: {mode} (第{col_idx}列)")

    # ========== 无模板时自动生成简版 ==========

    @staticmethod
    def _create_minimal_workbook():
        wb = openpyxl.Workbook()
        header_font = Font(name="微软雅黑", bold=True, size=11)
        thin_border = Border(
            left=Side(style="thin"), right=Side(style="thin"),
            top=Side(style="thin"), bottom=Side(style="thin"),
        )

        ws1 = wb.active
        ws1.title = "相关项功能清单"
        ri_headers = [
            "整车功能ID", "整车层级功能", "Feature List ID",
            "功能描述", "是否进行HARA分析",
            "相关项定义文档", "相关项内对应章节", "备注",
        ]
        for c, h in enumerate(ri_headers, 1):
            cell = ws1.cell(row=1, column=c, value=h)
            cell.font = header_font
            cell.border = thin_border
            cell.alignment = Alignment(horizontal="center", vertical="center")
        widths = [18, 22, 16, 40, 16, 30, 22, 30]
        for c, w in enumerate(widths, 1):
            ws1.column_dimensions[openpyxl.utils.get_column_letter(c)].width = w

        ws2 = wb.create_sheet("失效模式")
        fm_headers = ["整车功能ID", "功能异常\n整车功能"] + FAILURE_MODES + ["选择理由"]
        for c, h in enumerate(fm_headers, 1):
            cell = ws2.cell(row=1, column=c, value=h)
            cell.font = header_font
            cell.border = thin_border
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        fm_widths = [18, 22] + [6] * len(FAILURE_MODES) + [50]
        for c, w in enumerate(fm_widths, 1):
            ws2.column_dimensions[openpyxl.utils.get_column_letter(c)].width = w

        return wb
