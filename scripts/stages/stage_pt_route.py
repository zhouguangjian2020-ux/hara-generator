#!/usr/bin/env python3
"""
LEGACY / NON-PRODUCTION：该模块不再由 run_hara.py 暴露。
仅保留用于审计旧设计；PT 生产流程必须使用标准 S1→S2→S3→S4→S5。

PT域路由器 - 功能级匹配分流

在parse之后，对每个功能：
- 命中: 直接从PT_complete.json提取完整数据 → 跳过S1/S2/S3/S4
- 未命中: 进入原有管道 → S1→S2→S3→S4
"""

import sys
import json
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding='utf-8')

_SCRIPT_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from utils.function_matcher import match_function_in_pt_pack
from utils.pt_data_extractor import extract_complete_function_data
from utils.domain_packs import load_domain_pack


def route_pt_functions(intermediate_path: str, output_matched: str, output_unmatched: str):
    """
    PT域功能级匹配分流

    参数:
        intermediate_path: intermediate.json路径
        output_matched: 命中功能的输出路径（完整HARA数据）
        output_unmatched: 未命中功能的输出路径（需要走管道的功能列表）
    """
    # 读取intermediate.json
    with open(intermediate_path, 'r', encoding='utf-8') as f:
        intermediate = json.load(f)

    domain = intermediate.get('domain', '')
    if domain.upper() not in ['PT', 'P', 'POWERTRAIN']:
        print(f"⚠️  非PT域({domain})，跳过功能级匹配")
        return

    # 统一通过 Domain Pack 加载器读取已发布 PT 资产。
    # 旧 route 的数据模型仍假设 PT_complete.json 的旧结构，不能直接消费新的
    # 语义 Pack；迁移完成前必须失败关闭，不能输出看似成功的错误预填。
    print(f"\n[PT域路由] 加载已校验的 PT Domain Pack...")
    pt_pack = load_domain_pack("PT")
    if pt_pack.get("status") != "legacy_route_compatible":
        raise RuntimeError(
            "旧版 pt-route 已禁用：当前 PT Domain Pack 使用语义资产结构，"
            "请使用标准 S1→S2→S3→S4→S5 链路；禁止回退 PT_complete.json。"
        )

    matched_functions = []  # 命中的功能（直接提取数据）
    unmatched_functions = []  # 未命中的功能（需要走管道）

    print(f"\n[PT域路由] 开始功能级匹配...")
    print(f"总功能数: {len(intermediate['related_items'])}")

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
            # 命中：提取完整数据
            print(f"  ✓ 命中，提取历史数据...")

            function_id = match_result['function_id']
            complete_data = extract_complete_function_data(function_id, pt_pack)

            print(f"    失效模式: {len(complete_data['failure_modes'])} 种")
            print(f"    HAZOP: {len(complete_data['hazop_entries'])} 条")
            print(f"    HARA: {len(complete_data['hara_events'])} 条")
            print(f"    安全目标: {len(complete_data['safety_goals'])} 个")

            # 转换为最终JSON格式
            matched_data = _convert_to_final_format(
                func_id, func_name, complete_data, item
            )
            matched_functions.append(matched_data)

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

    # 保存结果
    print(f"\n[PT域路由] 保存结果...")
    print(f"  命中功能: {len(matched_functions)} 个")
    print(f"  未命中功能: {len(unmatched_functions)} 个")

    with open(output_matched, 'w', encoding='utf-8') as f:
        json.dump({
            'domain': domain,
            'matched_functions': matched_functions,
            'stats': {
                'total': len(intermediate['related_items']),
                'matched': len(matched_functions),
                'unmatched': len(unmatched_functions)
            }
        }, f, ensure_ascii=False, indent=2)

    with open(output_unmatched, 'w', encoding='utf-8') as f:
        json.dump({
            'domain': domain,
            'unmatched_functions': unmatched_functions,
            'original_intermediate': intermediate  # 保留原始数据供管道使用
        }, f, ensure_ascii=False, indent=2)

    print(f"\n✓ PT域路由完成")
    print(f"  命中数据: {output_matched}")
    print(f"  未命中列表: {output_unmatched}")


def _convert_to_final_format(func_id: str, func_name: str, complete_data: dict, original_item: dict) -> dict:
    """
    将PT_complete.json的数据转换为S4 hara validate输出的格式

    参数:
        func_id: 功能ID
        func_name: 功能名
        complete_data: 从PT_complete提取的完整数据
        original_item: intermediate.json中的原始功能数据

    返回:
        S4格式的HARA数据
    """
    # 按失效模式组织HAZOP和HARA
    hazards = []

    # 获取所有失效模式
    failure_modes = complete_data['failure_modes']

    for fm in failure_modes:
        # 查找该失效模式的HAZOP条目
        hazop_for_fm = [
            h for h in complete_data['hazop_entries']
            if h.get('failure_mode') == fm
        ]

        # 为每个HAZOP条目创建一个hazard组
        for hazop in hazop_for_fm:
            # 通过failure_id和vehicle_hazard_id关联HARA事件
            failure_id = hazop.get('failure_id')
            vehicle_hazard_id = hazop.get('vehicle_hazard_id')

            events = []
            for hara in complete_data['hara_events']:
                # 匹配条件：failure_id相同 且 vehicle_hazard_id相同
                if (hara.get('failure_id') == failure_id and
                    hara.get('vehicle_hazard_id') == vehicle_hazard_id):
                    events.append({
                        'scenario': hara.get('scenario', ''),
                        'description': hara.get('description', ''),
                        'severity': hara.get('severity'),
                        'severity_reason': hara.get('severity_reason', ''),
                        'exposure': hara.get('exposure'),
                        'exposure_reason': hara.get('exposure_reason', ''),
                        'controllability': hara.get('controllability'),
                        'controllability_reason': hara.get('controllability_reason', ''),
                        'expected_asil': hara.get('expected_asil'),
                        'safety_goal': hara.get('safety_goal'),
                        'safe_state': hara.get('safe_state'),
                        'ftti': hara.get('ftti'),
                        'source': 'pt_complete_exact_match'
                    })

            if events:
                hazards.append({
                    'failure_mode': fm,
                    'anomaly': hazop.get('anomaly', ''),
                    'vehicle_hazard': hazop.get('vehicle_hazard', ''),
                    'vehicle_hazard_id': vehicle_hazard_id,
                    'hazard_family': hazop.get('hazard_family', ''),
                    'events': events,
                    'source': 'pt_complete_exact_match'
                })

    return {
        'func_id': func_id,
        'func_name': func_name,
        'failure_modes': failure_modes,
        'hazards': hazards,
        'safety_goals': complete_data['safety_goals'],
        'sub_functions': original_item.get('sub_functions', []),
        'source': 'pt_complete_exact_match',
        'validation': {
            'passed': True,
            'message': 'PT域精确匹配，历史数据已验证'
        }
    }


def run(args):
    """命令行入口"""
    intermediate = args.intermediate
    output_matched = args.output_matched or 'output/pt_matched.json'
    output_unmatched = args.output_unmatched or 'output/pt_unmatched.json'

    route_pt_functions(intermediate, output_matched, output_unmatched)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='PT域功能级匹配分流')
    parser.add_argument('intermediate', help='intermediate.json路径')
    parser.add_argument('--output-matched', help='命中功能输出路径')
    parser.add_argument('--output-unmatched', help='未命中功能输出路径')

    args = parser.parse_args()
    run(args)
