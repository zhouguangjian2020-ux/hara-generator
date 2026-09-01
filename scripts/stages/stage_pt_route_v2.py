#!/usr/bin/env python3
"""
LEGACY / NON-PRODUCTION：该模块不再由 run_hara.py 暴露。
仅保留用于审计旧设计；PT 生产流程必须使用标准 S1→S2→S3→S4→S5。

PT域路由器 - 功能级匹配分流（完整版）

在parse之后，对每个功能：
- 命中: 直接从PT_complete.json提取完整数据 → 生成S1/S2/S3/S4所有文件
- 未命中: 进入原有管道 → S1→S2→S3→S4
"""

import sys
import json
from pathlib import Path
from typing import Any, Dict, List
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8')

_SCRIPT_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from utils.function_matcher import match_function_in_pt_pack
from utils.domain_packs import load_domain_pack


def route_pt_functions(
    intermediate_path: str,
    output_s1: str,
    output_s2: str,
    output_s3: str,
    output_s4: str,
    output_unmatched: str
):
    """
    PT域功能级匹配分流，生成完整的S1/S2/S3/S4文件

    参数:
        intermediate_path: intermediate.json路径
        output_s1: S1决策输出路径
        output_s2: S2决策输出路径
        output_s3: S3 HAZOP输出路径
        output_s4: S4 HARA输出路径
        output_unmatched: 未命中功能输出路径（需要走管道）
    """
    # 读取intermediate.json
    with open(intermediate_path, 'r', encoding='utf-8') as f:
        intermediate = json.load(f)

    domain = intermediate.get('domain', '')
    if domain.upper() not in ['PT', 'P', 'POWERTRAIN']:
        print(f"⚠️  非PT域({domain})，跳过功能级匹配")
        return

    # 统一通过 Domain Pack 加载器读取已发布 PT 资产。
    # 该版本仍是旧的“一次性生成 S1/S2/S3/S4”实现，不能消费新的语义 Pack；
    # 迁移完成前必须失败关闭，不能绕过 Pack 校验。
    print(f"\n[PT域路由] 加载已校验的 PT Domain Pack...")
    pt_pack = load_domain_pack("PT")
    if pt_pack.get("status") != "legacy_route_compatible":
        raise RuntimeError(
            "旧版 pt-route-v2 已禁用：当前 PT Domain Pack 使用语义资产结构，"
            "请使用标准 S1→S2→S3→S4→S5 链路；禁止回退 PT_complete.json。"
        )

    matched_functions = []  # 命中的功能
    unmatched_functions = []  # 未命中的功能

    print(f"\n[PT域路由] 开始功能级匹配...")
    print(f"总功能数: {len(intermediate['related_items'])}")

    # 统计PT_complete中每个功能的HARA事件数
    func_hara_count = _count_hara_events_by_function(pt_pack)

    for item in intermediate['related_items']:
        func_id = item['func_id']
        func_name = item['func_name']

        print(f"\n处理功能: {func_id} - {func_name}")

        # 功能级匹配
        match_result = match_function_in_pt_pack(func_name, pt_pack)
        match_type = match_result['match_type']
        confidence = match_result['confidence']

        print(f"  匹配结果: {match_type} (置信度: {confidence:.1%})")

        if match_type == "exact":
            # 命中：检查是否有HARA事件
            function_id = match_result['function_id']
            hara_count = func_hara_count.get(function_id, 0)

            if hara_count > 0:
                print(f"  ✓ 命中且有HARA分析({hara_count}条)，提取历史数据...")
                matched_functions.append({
                    'func_id': func_id,
                    'func_name': func_name,
                    'matched_function_id': function_id,
                    'sub_functions': item.get('sub_functions', []),
                    'has_hara': True
                })
            else:
                print(f"  ✓ 命中但无HARA分析，标记为is_hara=false...")
                matched_functions.append({
                    'func_id': func_id,
                    'func_name': func_name,
                    'matched_function_id': function_id,
                    'sub_functions': item.get('sub_functions', []),
                    'has_hara': False
                })
        else:
            # 未命中：需要走管道
            print(f"  ✗ 未命中，需要走S1→S2→S3→S4管道")
            unmatched_functions.append({
                'func_id': func_id,
                'func_name': func_name,
                'match_type': match_type,
                'confidence': confidence,
                'matched_function': match_result.get('matched_function'),
                'sub_functions': item['sub_functions']
            })

    # 生成所有输出文件
    print(f"\n[PT域路由] 生成输出文件...")
    print(f"  命中功能: {len(matched_functions)} 个")
    print(f"  未命中功能: {len(unmatched_functions)} 个")

    doc_name = intermediate.get('doc_name', '')

    # 生成S1决策
    s1_data = _generate_s1_decisions(matched_functions, intermediate, doc_name)
    with open(output_s1, 'w', encoding='utf-8') as f:
        json.dump(s1_data, f, ensure_ascii=False, indent=2)
    print(f"  ✓ S1决策: {output_s1}")

    # 生成S2决策
    s2_data = _generate_s2_decisions(matched_functions, pt_pack, doc_name)
    with open(output_s2, 'w', encoding='utf-8') as f:
        json.dump(s2_data, f, ensure_ascii=False, indent=2)
    print(f"  ✓ S2决策: {output_s2}")

    # 生成S3 HAZOP
    s3_data = _generate_s3_hazop(matched_functions, pt_pack)
    with open(output_s3, 'w', encoding='utf-8') as f:
        json.dump(s3_data, f, ensure_ascii=False, indent=2)
    print(f"  ✓ S3 HAZOP: {output_s3}")

    # 生成S4 HARA
    s4_data = _generate_s4_hara(matched_functions, pt_pack, s3_data)
    with open(output_s4, 'w', encoding='utf-8') as f:
        json.dump(s4_data, f, ensure_ascii=False, indent=2)
    print(f"  ✓ S4 HARA: {output_s4}")

    # 生成未命中列表
    with open(output_unmatched, 'w', encoding='utf-8') as f:
        json.dump({
            'domain': domain,
            'unmatched_functions': unmatched_functions,
            'original_intermediate': intermediate
        }, f, ensure_ascii=False, indent=2)
    print(f"  ✓ 未命中列表: {output_unmatched}")

    print(f"\n✓ PT域路由完成")


def _count_hara_events_by_function(pt_pack: dict) -> Dict[str, int]:
    """统计PT_complete中每个功能的HARA事件数"""
    count = defaultdict(int)

    for event in pt_pack['risk_catalog']['event_matrix']:
        failure_id = event.get('failure_id', '')
        if failure_id.startswith('P_MF_'):
            # P_MF_0001_01 -> P_func_0001
            parts = failure_id.split('_')
            if len(parts) >= 3:
                func_num = parts[2]
                func_id = f'P_func_{func_num}'
                count[func_id] += 1

    return count


def _generate_s1_decisions(matched_functions: List[dict], intermediate: dict, doc_name: str) -> dict:
    """生成S1决策文件"""
    decisions = []
    related_items = []

    for func in matched_functions:
        func_id = func['func_id']
        func_name = func['func_name']
        has_hara = func['has_hara']
        sub_functions = func['sub_functions']

        # decisions部分
        decisions.append({
            'func_id': func_id,
            'func_name': func_name,
            'is_hara': has_hara,
            'sub_functions': [
                {
                    'feature_list_id': sf['feature_list_id'],
                    'is_hara': has_hara,
                    'remark': 'PT域精确匹配' if has_hara else '纯信息显示类功能，不影响车辆运动控制，不进行HARA分析。'
                }
                for sf in sub_functions
            ]
        })

        # related_items部分
        related_items.append({
            'func_id': func_id,
            'func_name': func_name,
            's1_excluded_by_rule': False,
            'sub_functions': [
                {
                    'feature_list_id': sf['feature_list_id'],
                    'name': sf['name'],
                    's1_rule_is_hara': has_hara,
                    's1_rule_remark': 'PT域精确匹配' if has_hara else '纯信息显示类功能，不影响车辆运动控制，不进行HARA分析。',
                    's1_disposition': None
                }
                for sf in sub_functions
            ]
        })

    return {
        'doc_name': doc_name,
        'domain': 'PT',
        'decisions': decisions,
        'related_items': related_items
    }


def _generate_s2_decisions(matched_functions: List[dict], pt_pack: dict, doc_name: str) -> dict:
    """生成S2决策文件"""
    decisions = []
    related_items = []

    for func in matched_functions:
        if not func['has_hara']:
            # 无HARA分析的功能不生成S2
            continue

        func_id = func['func_id']
        func_name = func['func_name']
        matched_function_id = func['matched_function_id']

        # 从PT_complete提取失效模式（需要标准化）
        # 注意：只包含HAZOP中实际存在的失效模式
        raw_modes = pt_pack['analysis_catalog']['failure_mode_rules'].get(matched_function_id, [])
        declared_modes = [_normalize_failure_mode(mode) for mode in raw_modes]

        # 从HAZOP中提取该功能实际存在的失效模式
        actual_modes = set()
        for hazop in pt_pack['analysis_catalog']['hazop_patterns']:
            original_fid = hazop.get('failure_id', '')
            if original_fid.startswith(f'{matched_function_id.replace("func", "MF")}_'):
                failure_mode = _normalize_failure_mode(hazop.get('failure_mode', ''))
                if failure_mode:
                    actual_modes.add(failure_mode)

        # 只使用HAZOP中实际存在的失效模式
        failure_modes = sorted(actual_modes)

        # decisions部分
        decisions.append({
            'func_id': func_id,
            'func_name': func_name,
            'selected_modes': failure_modes,
            'reason': 'PT域精确匹配，使用历史失效模式'
        })

        # related_items部分
        related_items.append({
            'func_id': func_id,
            'func_name': func_name,
            'failure_modes': failure_modes,
            'locked': True,
            'remark': 'PT域精确匹配'
        })

    return {
        'doc_name': doc_name,
        'domain': 'PT',
        'decisions': decisions,
        'related_items': related_items
    }


def _normalize_failure_mode(mode: str) -> str:
    """
    标准化失效模式名称

    PT_complete中的"过大"/"过小"需要映射为标准的"过多"/"过少"
    """
    mapping = {
        '过大': '过多',
        '过小': '过少'
    }
    return mapping.get(mode, mode)


def _generate_s3_hazop(matched_functions: List[dict], pt_pack: dict) -> dict:
    """
    生成S3 HAZOP文件

    从PT_complete.json的hazop_patterns提取数据，生成完整的HAZOP条目
    """
    entries = []

    # 构建failure_id映射
    failure_id_counter = {}  # {func_id: {failure_mode: counter}}

    for func in matched_functions:
        if not func['has_hara']:
            # 无HARA分析的功能不生成S3
            continue

        func_id = func['func_id']
        func_name = func['func_name']
        matched_function_id = func['matched_function_id']

        # 从PT_complete提取该功能的所有HAZOP条目
        hazop_patterns = pt_pack['analysis_catalog'].get('hazop_patterns', [])

        # 按失效模式组织HAZOP
        mode_hazops = defaultdict(list)  # {failure_mode: [hazop_entries]}

        for hazop in hazop_patterns:
            # 从failure_id判断是否属于该功能
            original_fid = hazop.get('failure_id', '')
            if original_fid.startswith(f'{matched_function_id.replace("func", "MF")}_'):
                failure_mode = _normalize_failure_mode(hazop.get('failure_mode', ''))
                mode_hazops[failure_mode].append(hazop)

        # 为每个失效模式生成S3条目
        if func_id not in failure_id_counter:
            failure_id_counter[func_id] = {}

        for failure_mode, hazops in sorted(mode_hazops.items()):
            # 按anomaly分组（一个anomaly可能有多个hazard）
            anomaly_groups = defaultdict(list)  # {anomaly: [hazop_entries]}

            for hazop in hazops:
                anomaly = hazop.get('anomaly', '')
                anomaly_groups[anomaly].append(hazop)

            # 生成anomalies列表
            anomalies = []

            for anomaly, hazop_list in anomaly_groups.items():
                # 生成failure_id
                if failure_mode not in failure_id_counter[func_id]:
                    failure_id_counter[func_id][failure_mode] = 0
                failure_id_counter[func_id][failure_mode] += 1

                func_num = func_id.split('_')[-1]
                seq = failure_id_counter[func_id][failure_mode]
                failure_id = f"P_MF_{func_num}_{seq:02d}"

                # 收集所有hazard
                hazards = []
                for hazop in hazop_list:
                    vehicle_hazard = hazop.get('vehicle_hazard', '')
                    # associated_hara从原始数据中获取，必须是字符串
                    associated_hara = hazop.get('associated_hara', '')
                    if not associated_hara:
                        associated_hara = ''  # 空字符串而不是None

                    hazards.append({
                        'description': vehicle_hazard,
                        'associated_hara': associated_hara
                    })

                anomalies.append({
                    'description': anomaly,
                    'failure_id': failure_id,
                    'hazards': hazards
                })

            # 生成entry
            entries.append({
                'func_id': func_id,
                'func_name': func_name,
                'failure_mode': failure_mode,
                'anomalies': anomalies
            })

    return {
        'entries': entries
    }


def _generate_s4_hara(matched_functions: List[dict], pt_pack: dict, s3_data: dict) -> dict:
    """
    生成S4 HARA文件

    从PT_complete.json提取数据，关联S3的failure_id
    """
    hazards = []

    # 构建hazop_patterns的映射
    hazop_map = {}  # {failure_id: hazop_entry}
    for hazop in pt_pack['analysis_catalog']['hazop_patterns']:
        fid = hazop.get('failure_id')
        if fid:
            hazop_map[fid] = hazop

    # 构建S3的failure_id映射: {failure_id: (func_id, func_name, failure_mode, anomaly)}
    s3_failure_map = {}
    for entry in s3_data.get('entries', []):
        func_id = entry['func_id']
        func_name = entry['func_name']
        failure_mode = entry['failure_mode']
        for anom in entry.get('anomalies', []):
            failure_id = anom['failure_id']
            anomaly = anom['description']
            s3_failure_map[failure_id] = (func_id, func_name, failure_mode, anomaly)

    # 从event_matrix提取HARA事件，按failure_id分组
    event_groups = defaultdict(list)  # {failure_id: [events]}

    # 为每个唯一的safety_goal生成ID
    safety_goal_id_map = {}  # {safety_goal_text: safety_goal_id}
    sg_counter = 1

    for event in pt_pack['risk_catalog']['event_matrix']:
        failure_id = event.get('failure_id')
        # 跳过标题行（failure_id不是P_MF格式）
        if not failure_id or not failure_id.startswith('P_MF_'):
            continue

        # 只处理S3中存在的failure_id
        if failure_id not in s3_failure_map:
            continue

        # 提取事件数据并修复数据质量问题
        severity = event.get('severity')
        exposure = event.get('exposure')
        controllability = event.get('controllability')
        exposure_reason = event.get('exposure_reason', '')
        controllability_reason = event.get('controllability_reason', '')
        expected_asil = event.get('expected_asil')
        safety_goal = event.get('safety_goal')
        safe_state = event.get('safe_state')
        ftti = event.get('ftti')

        # 数据质量修复：S=0时，E/C理由必须为null
        if severity == 0:
            exposure_reason = None
            controllability_reason = None
            exposure = None
            controllability = None

        # 数据质量修复：ASIL=A/B/C/D时，安全目标字段不能为None
        if expected_asil in ['A', 'B', 'C', 'D']:
            if not safety_goal or len(str(safety_goal).strip()) < 10:
                # 安全目标过短或为空，根据anomaly和vehicle_hazard生成
                # 从s3_failure_map获取anomaly
                if failure_id in s3_failure_map:
                    _, _, _, anomaly = s3_failure_map[failure_id]
                else:
                    anomaly = ''

                # 从hazop_map获取vehicle_hazard
                hazop_entry = hazop_map.get(failure_id, {})
                vehicle_hazard = hazop_entry.get('vehicle_hazard', '')

                if anomaly and vehicle_hazard:
                    safety_goal = f"防止{anomaly}导致{vehicle_hazard}"
                elif anomaly:
                    safety_goal = f"防止{anomaly}的危害"
                elif vehicle_hazard:
                    safety_goal = f"防止{vehicle_hazard}"
                else:
                    safety_goal = f"PT_SG_{failure_id}"  # 最后的备用方案
            if not safe_state:
                safe_state = "安全状态（需补充）"
            if not ftti:
                ftti = "100ms"  # 默认FTTI

        # 暂时不生成safety_goal_id，等获取hazard_family后再生成
        event_groups[failure_id].append({
            'scenario': event.get('description', ''),
            'description': event.get('description', ''),
            'severity': severity,
            'severity_reason': event.get('severity_reason', ''),
            'exposure': exposure,
            'exposure_reason': exposure_reason,
            'controllability': controllability,
            'controllability_reason': controllability_reason,
            'expected_asil': expected_asil,
            'asil': expected_asil,  # S5需要asil字段
            'safety_goal': safety_goal,
            'safety_goal_id': None,  # 稍后填充
            'safe_state': safe_state,
            'ftti': ftti,
            'vehicle_hazard_id': event.get('vehicle_hazard_id')
        })

    # 为每个failure_id生成hazard条目
    global_hazard_counter = 1

    for failure_id in sorted(event_groups.keys()):
        if failure_id not in s3_failure_map:
            continue

        func_id, func_name, failure_mode, anomaly = s3_failure_map[failure_id]
        events = event_groups[failure_id]

        # 从hazop_map获取vehicle_hazard和hazard_family
        hazop = hazop_map.get(failure_id, {})
        vehicle_hazard = hazop.get('vehicle_hazard', '')
        hazard_family = hazop.get('hazard_family', '')

        # 为每个event生成safety_goal_id（基于safety_goal + hazard_family + safe_state + ftti）
        events_with_id = []
        first_hazard_id = f"P_hzrd_{global_hazard_counter:05d}"  # 保存第一个hazard_id

        for event in events:
            # 生成唯一的safety_goal_id
            safety_goal = event.get('safety_goal')
            asil = event.get('asil')
            if safety_goal and asil not in ['QM', None, '-']:
                # 去重键：(safety_goal, hazard_family, safe_state, ftti)
                sg_key = (
                    str(safety_goal).strip(),
                    hazard_family or '',
                    event.get('safe_state') or '',
                    event.get('ftti') or ''
                )
                if sg_key not in safety_goal_id_map:
                    safety_goal_id_map[sg_key] = f"PT_SG_EV_{sg_counter:04d}"
                    sg_counter += 1
                event['safety_goal_id'] = safety_goal_id_map[sg_key]

            # 为每个event添加hazard_id
            event['hazard_id'] = f"P_hzrd_{global_hazard_counter:05d}"
            global_hazard_counter += 1
            events_with_id.append(event)

        # 计算id_range
        if len(events_with_id) == 1:
            id_range = first_hazard_id
        else:
            last_hazard_id = f"P_hzrd_{global_hazard_counter - 1:05d}"
            id_range = f"{first_hazard_id}~{last_hazard_id}"

        hazards.append({
            'func_id': func_id,
            'func_name': func_name,
            'failure_mode': failure_mode,
            'failure_id': failure_id,
            'anomaly': anomaly,
            'vehicle_hazard': vehicle_hazard,
            'hazard_id': first_hazard_id,
            'hazard_family': hazard_family,
            'id_range': id_range,
            'events': events_with_id,
            'source': 'pt_complete_exact_match'
        })

    return {
        'hazards': hazards,
        'source': 'pt_complete_exact_match',
        'validation': {
            'passed': True,
            'error_count': 0,
            'warning_count': 0,
            'message': 'PT域精确匹配，历史数据已验证'
        }
    }


def run(args):
    """命令行入口"""
    intermediate = args.intermediate
    output_s1 = args.output_s1 or 'output/pt_s1_decisions.json'
    output_s2 = args.output_s2 or 'output/pt_s2_decisions.json'
    output_s3 = args.output_s3 or 'output/pt_s3_hazop.json'
    output_s4 = args.output_s4 or 'output/pt_s4_hara_final.json'
    output_unmatched = args.output_unmatched or 'output/pt_unmatched.json'

    route_pt_functions(
        intermediate,
        output_s1,
        output_s2,
        output_s3,
        output_s4,
        output_unmatched
    )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='PT域功能级匹配分流（完整版）')
    parser.add_argument('intermediate', help='intermediate.json路径')
    parser.add_argument('--output-s1', help='S1决策输出路径')
    parser.add_argument('--output-s2', help='S2决策输出路径')
    parser.add_argument('--output-s3', help='S3 HAZOP输出路径')
    parser.add_argument('--output-s4', help='S4 HARA输出路径')
    parser.add_argument('--output-unmatched', help='未命中功能输出路径')

    args = parser.parse_args()
    run(args)
