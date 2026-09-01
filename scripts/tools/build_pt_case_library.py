"""
构建PT域完整案例库（关联HAZOP失效模式）
"""
import openpyxl
import json
import sys
from pathlib import Path
from collections import defaultdict

def extract_hazop_mapping(excel_path):
    """从HAZOP表提取失效ID到失效模式的映射"""
    wb = openpyxl.load_workbook(excel_path, data_only=True)

    # 找到较大的HAZOP表
    hazop_sheet = None
    for name in wb.sheetnames:
        sheet = wb[name]
        if 'HAZOP' in name.upper() and sheet.max_row > 100:
            hazop_sheet = sheet
            print(f'Found HAZOP sheet: {repr(name)}, rows: {sheet.max_row}')
            break

    if not hazop_sheet:
        print('ERROR: Cannot find HAZOP sheet')
        return {}

    # 列映射（根据观察）
    COL_FUNC_ID = 1
    COL_FUNC_NAME = 2
    COL_FAILURE_MODE = 3
    COL_ANOMALY = 4
    COL_FAILURE_ID = 5
    COL_VEHICLE_HAZARD = 6
    COL_HARA_IDS = 7

    mapping = {}  # failure_id -> {func_id, func_name, failure_mode, anomaly}

    current_func_id = None
    current_func_name = None
    current_failure_mode = None

    for row_idx in range(3, hazop_sheet.max_row + 1):
        func_id = hazop_sheet.cell(row_idx, COL_FUNC_ID).value
        func_name = hazop_sheet.cell(row_idx, COL_FUNC_NAME).value
        failure_mode = hazop_sheet.cell(row_idx, COL_FAILURE_MODE).value
        anomaly = hazop_sheet.cell(row_idx, COL_ANOMALY).value
        failure_id = hazop_sheet.cell(row_idx, COL_FAILURE_ID).value

        # 合并单元格处理：保持上一个非空值
        if func_id and isinstance(func_id, str) and func_id.startswith('P_func_'):
            current_func_id = func_id.strip()
        if func_name and isinstance(func_name, str):
            current_func_name = func_name.strip()
        if failure_mode and isinstance(failure_mode, str):
            current_failure_mode = failure_mode.strip()

        # 清洗数据
        if anomaly:
            anomaly = str(anomaly).strip()
        if failure_id and isinstance(failure_id, str) and failure_id.startswith('P_MF_'):
            failure_id = failure_id.strip()

            mapping[failure_id] = {
                'func_id': current_func_id,
                'func_name': current_func_name,
                'failure_mode': current_failure_mode,
                'anomaly': anomaly
            }

    print(f'Extracted {len(mapping)} failure_id mappings from HAZOP')
    return mapping


def extract_hara_cases(excel_path):
    """从HARA表提取案例（复用之前的逻辑）"""
    wb = openpyxl.load_workbook(excel_path, data_only=True)

    hara_sheet = None
    for name in wb.sheetnames:
        if 'HARA' in name.upper() and len(name) < 15:
            hara_sheet = wb[name]
            print(f'Found HARA sheet: {repr(name)}')
            break

    if not hara_sheet:
        print("ERROR: Cannot find HARA sheet")
        return []

    print(f'Total rows: {hara_sheet.max_row}')

    # 列映射
    cases = []
    data_start_row = 3

    for row_idx in range(data_start_row, hara_sheet.max_row + 1):
        hazard_id = hara_sheet.cell(row_idx, 1).value
        func_name = hara_sheet.cell(row_idx, 2).value
        failure_id = hara_sheet.cell(row_idx, 3).value
        anomaly = hara_sheet.cell(row_idx, 4).value
        vehicle_hazard = hara_sheet.cell(row_idx, 5).value
        scenario = hara_sheet.cell(row_idx, 6).value
        hazard_event = hara_sheet.cell(row_idx, 7).value
        severity = hara_sheet.cell(row_idx, 8).value
        s_reason = hara_sheet.cell(row_idx, 9).value
        exposure = hara_sheet.cell(row_idx, 10).value
        e_reason = hara_sheet.cell(row_idx, 11).value
        controllability = hara_sheet.cell(row_idx, 12).value
        c_reason = hara_sheet.cell(row_idx, 13).value
        asil = hara_sheet.cell(row_idx, 14).value
        sg_id = hara_sheet.cell(row_idx, 15).value
        safety_goal = hara_sheet.cell(row_idx, 16).value
        safe_state = hara_sheet.cell(row_idx, 17).value
        ftti = hara_sheet.cell(row_idx, 18).value

        # 跳过空行和非数据行
        if not hazard_id or not isinstance(hazard_id, str):
            continue
        if not hazard_id.startswith('P_hzrd_'):
            continue

        # 数据清洗
        def clean_str(val):
            if val is None:
                return None
            s = str(val).strip()
            return s if s else None

        def clean_int(val):
            if val is None or val == '':
                return None
            try:
                return int(val)
            except:
                return None

        case = {
            'case_id': hazard_id,
            'domain': 'PT',
            'func_name': clean_str(func_name),
            'failure_id': clean_str(failure_id),
            'anomaly': clean_str(anomaly),
            'vehicle_hazard': clean_str(vehicle_hazard),
            'scenario': clean_str(scenario),
            'hazard_event_desc': clean_str(hazard_event),
            'severity': clean_int(severity),
            'severity_reason': clean_str(s_reason),
            'exposure': clean_int(exposure),
            'exposure_reason': clean_str(e_reason),
            'controllability': clean_int(controllability),
            'controllability_reason': clean_str(c_reason),
            'asil': clean_str(asil),
            'safety_goal_id': clean_str(sg_id),
            'safety_goal': clean_str(safety_goal),
            'safe_state': clean_str(safe_state),
            'ftti': clean_str(ftti)
        }

        # 跳过不完整的案例
        if not case['anomaly'] or not case['scenario']:
            continue

        # S=0特殊处理
        if case['severity'] == 0:
            case['exposure'] = None
            case['exposure_reason'] = None
            case['controllability'] = None
            case['controllability_reason'] = None
            case['asil'] = None
            case['safety_goal'] = None
            case['safety_goal_id'] = None
            case['safe_state'] = None
            case['ftti'] = None

        cases.append(case)

    print(f'Extracted {len(cases)} HARA cases')
    return cases


def merge_hazop_and_hara(hazop_mapping, hara_cases):
    """合并HAZOP失效模式映射和HARA案例"""
    merged = []
    matched = 0
    unmatched = 0

    for case in hara_cases:
        failure_id = case.get('failure_id')

        # 从HAZOP映射中获取失效模式信息
        if failure_id and failure_id in hazop_mapping:
            hazop_info = hazop_mapping[failure_id]
            case['func_id'] = hazop_info['func_id']
            case['failure_mode'] = hazop_info['failure_mode']
            # 优先使用HAZOP的异常描述（更标准）
            if hazop_info['anomaly']:
                case['anomaly_from_hazop'] = hazop_info['anomaly']
            matched += 1
        else:
            unmatched += 1
            case['func_id'] = None
            case['failure_mode'] = None

        merged.append(case)

    print(f'\nMerge results:')
    print(f'  Matched with HAZOP: {matched}')
    print(f'  Unmatched: {unmatched}')

    return merged


def analyze_case_library(cases):
    """分析案例库统计"""
    print('\n=== PT域案例库统计 ===')
    print(f'总案例数: {len(cases)}')

    # 按功能统计
    by_func = defaultdict(list)
    for case in cases:
        func = case.get('func_name') or case.get('func_id') or 'Unknown'
        by_func[func].append(case)

    print(f'\n功能数: {len(by_func)}')
    for func, func_cases in sorted(by_func.items(), key=lambda x: len(x[1]), reverse=True)[:10]:
        print(f'  {func}: {len(func_cases)} 案例')

    # 按失效模式统计
    by_mode = defaultdict(list)
    for case in cases:
        mode = case.get('failure_mode') or 'Unknown'
        by_mode[mode].append(case)

    print(f'\n失效模式: {len(by_mode)}')
    for mode, mode_cases in sorted(by_mode.items(), key=lambda x: len(x[1]), reverse=True)[:10]:
        print(f'  {mode}: {len(mode_cases)} 案例')

    # 场景统计
    scenarios = set(case['scenario'] for case in cases if case.get('scenario'))
    print(f'\n独特场景数: {len(scenarios)}')

    # ASIL分布
    asil_dist = defaultdict(int)
    for case in cases:
        asil = case.get('asil') or 'NULL'
        asil_dist[asil] += 1

    print(f'\nASIL分布:')
    for asil in ['D', 'C', 'B', 'A', 'QM', 'NULL']:
        if asil in asil_dist:
            print(f'  {asil}: {asil_dist[asil]}')

    # S=0统计
    s0_count = sum(1 for c in cases if c.get('severity') == 0)
    print(f'\nS=0案例: {s0_count}')

    # 数据完整性
    print(f'\n=== 数据完整性 ===')
    has_func_id = sum(1 for c in cases if c.get('func_id'))
    has_failure_mode = sum(1 for c in cases if c.get('failure_mode'))
    has_scenario = sum(1 for c in cases if c.get('scenario'))
    has_sec = sum(1 for c in cases if c.get('severity') is not None and
                  (c.get('severity') == 0 or (c.get('exposure') is not None and c.get('controllability') is not None)))

    print(f'  有func_id: {has_func_id}/{len(cases)} ({has_func_id*100//len(cases)}%)')
    print(f'  有失效模式: {has_failure_mode}/{len(cases)} ({has_failure_mode*100//len(cases)}%)')
    print(f'  有场景: {has_scenario}/{len(cases)} ({has_scenario*100//len(cases)}%)')
    print(f'  S/E/C完整: {has_sec}/{len(cases)} ({has_sec*100//len(cases)}%)')


def main():
    excel_path = Path('关键数据/PT域/ALL-HARA_FUSA-100-GPFA-H002______FUSA HARA PT DOMAIN.xlsx')

    if not excel_path.exists():
        print(f'ERROR: File not found: {excel_path}')
        sys.exit(1)

    print('=' * 80)
    print('PT域案例库构建')
    print('=' * 80)

    # 步骤1：提取HAZOP映射
    print('\n[1/4] 提取HAZOP失效模式映射...')
    hazop_mapping = extract_hazop_mapping(excel_path)

    # 步骤2：提取HARA案例
    print('\n[2/4] 提取HARA案例...')
    hara_cases = extract_hara_cases(excel_path)

    if not hara_cases:
        print('ERROR: No cases extracted')
        sys.exit(1)

    # 步骤3：合并数据
    print('\n[3/4] 合并HAZOP和HARA数据...')
    cases = merge_hazop_and_hara(hazop_mapping, hara_cases)

    # 步骤4：统计分析
    print('\n[4/4] 分析案例库...')
    analyze_case_library(cases)

    # 保存完整案例库
    output_path = Path('references/case_library/PT_cases.json')
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(cases, f, ensure_ascii=False, indent=2)

    print(f'\n[OK] 案例库已保存: {output_path}')

    # 生成可读预览
    preview_path = Path('references/case_library/PT_cases_summary.txt')
    with open(preview_path, 'w', encoding='utf-8') as f:
        f.write('PT域案例库摘要\n')
        f.write('=' * 80 + '\n\n')
        f.write(f'总案例数: {len(cases)}\n')
        f.write(f'功能数: {len(set(c.get("func_id") for c in cases if c.get("func_id")))}\n')
        f.write(f'失效模式: {len(set(c.get("failure_mode") for c in cases if c.get("failure_mode")))}\n')
        f.write(f'场景数: {len(set(c.get("scenario") for c in cases if c.get("scenario")))}\n\n')

        f.write('前20个案例示例:\n')
        f.write('-' * 80 + '\n')
        for i, case in enumerate(cases[:20], 1):
            f.write(f'\n{i}. {case["case_id"]}\n')
            f.write(f'   功能: {case.get("func_name", "N/A")}\n')
            f.write(f'   失效模式: {case.get("failure_mode", "N/A")}\n')
            f.write(f'   异常: {case["anomaly"]}\n')
            f.write(f'   场景: {case["scenario"]}\n')
            f.write(f'   S/E/C: {case["severity"]}/{case["exposure"]}/{case["controllability"]}\n')
            f.write(f'   ASIL: {case.get("asil", "N/A")}\n')
            if case.get('safety_goal'):
                f.write(f'   安全目标: {case["safety_goal"]}\n')

    print(f'[OK] 摘要已保存: {preview_path}')

    print('\n' + '=' * 80)
    print('完成！')
    print('=' * 80)


if __name__ == '__main__':
    main()
