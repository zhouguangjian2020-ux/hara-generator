#!/usr/bin/env python3
"""
将能量回收功能数据整合到PT_complete.json
"""

import json
import sys
from pathlib import Path
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8')


def load_json(path):
    """加载JSON文件"""
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_json(data, path):
    """保存JSON文件"""
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def analyze_extracted_data(extracted_data):
    """分析提取的数据结构"""
    print('=' * 80)
    print('分析提取的能量回收功能数据')
    print('=' * 80)
    print()

    hara_events = extracted_data['hara_events']
    hazop_entries = extracted_data['hazop_entries']

    print(f'HARA事件: {len(hara_events)} 个')
    print(f'HAZOP条目: {len(hazop_entries)} 个')
    print()

    # 分析失效ID分布
    failure_ids = set(e['failure_id'] for e in hara_events if e['failure_id'])
    print(f'唯一失效ID: {len(failure_ids)} 个')
    for fid in sorted(failure_ids):
        count = sum(1 for e in hara_events if e['failure_id'] == fid)
        print(f'  {fid}: {count} 个事件')
    print()

    # 提取失效模式
    failure_modes_by_id = {}
    for hazop in hazop_entries:
        fid = hazop['failure_id']
        fm = hazop['failure_mode']
        if fid and fm:
            failure_modes_by_id[fid] = fm

    # 按失效模式分组
    fm_groups = defaultdict(list)
    for fid, fm in failure_modes_by_id.items():
        fm_groups[fm].append(fid)

    print('失效模式分布:')
    for fm, fids in sorted(fm_groups.items()):
        print(f'  {fm}: {fids}')
    print()

    return failure_ids, failure_modes_by_id


def merge_function_index(pt_complete, extracted_data):
    """
    合并function_index

    PT_complete结构:
    function_catalog.function_index = {
        "P_func_0001": "档位控制及显示",
        "P_func_0002": "整车高压上下电管理",
        ...
    }
    """
    print('[1] 合并 function_catalog.function_index')
    print('-' * 80)

    func_id = extracted_data['function_id']
    func_name = extracted_data['function_name']

    function_index = pt_complete['function_catalog']['function_index']

    if func_id in function_index:
        print(f'⚠️  {func_id} 已存在: {function_index[func_id]}')
        print(f'   将更新为: {func_name}')
    else:
        print(f'✓ 添加新功能: {func_id} = {func_name}')

    function_index[func_id] = func_name
    print()


def merge_failure_mode_rules(pt_complete, extracted_data, failure_modes_by_id):
    """
    合并failure_mode_rules

    PT_complete结构:
    analysis_catalog.failure_mode_rules = {
        "P_func_0001": ["反向", "丢失", "过少", "非预期", "过多"],
        ...
    }
    """
    print('[2] 合并 analysis_catalog.failure_mode_rules')
    print('-' * 80)

    func_id = extracted_data['function_id']

    # 提取所有失效模式
    failure_modes = list(set(failure_modes_by_id.values()))
    failure_modes.sort()

    failure_mode_rules = pt_complete['analysis_catalog']['failure_mode_rules']

    if func_id in failure_mode_rules:
        print(f'⚠️  {func_id} 已存在: {failure_mode_rules[func_id]}')
        print(f'   将更新为: {failure_modes}')
    else:
        print(f'✓ 添加失效模式规则: {func_id} = {failure_modes}')

    failure_mode_rules[func_id] = failure_modes
    print()


def merge_hazop_patterns(pt_complete, extracted_data):
    """
    合并hazop_patterns

    PT_complete结构:
    analysis_catalog.hazop_patterns = [
        {
            "failure_mode": "丢失",
            "failure_id": "P_MF_0001_01",
            "anomaly": "P档切入D档位失效",
            "hazard_family": "unexpected_longitudinal_motion",
            "vehicle_hazard": "车辆无扭矩输出",
            "vehicle_hazard_id": "P_hzrd_01001"
        },
        ...
    ]
    """
    print('[3] 合并 analysis_catalog.hazop_patterns')
    print('-' * 80)

    hazop_patterns = pt_complete['analysis_catalog']['hazop_patterns']
    hazop_entries = extracted_data['hazop_entries']

    # 获取已有的failure_id
    existing_fids = set(h['failure_id'] for h in hazop_patterns)

    added = 0
    updated = 0

    for hazop in hazop_entries:
        failure_id = hazop['failure_id']

        # 转换为PT_complete格式
        new_entry = {
            'failure_mode': hazop['failure_mode'],
            'failure_id': failure_id,
            'anomaly': hazop['anomaly'],
            'hazard_family': 'energy_recovery',  # 新的危害家族
            'vehicle_hazard': hazop['vehicle_hazard'],
            'vehicle_hazard_id': ''  # 将从HARA事件中提取
        }

        if failure_id in existing_fids:
            # 更新已有条目
            for i, h in enumerate(hazop_patterns):
                if h['failure_id'] == failure_id:
                    hazop_patterns[i] = new_entry
                    updated += 1
                    break
        else:
            # 添加新条目
            hazop_patterns.append(new_entry)
            added += 1

    print(f'✓ 新增HAZOP条目: {added}')
    print(f'✓ 更新HAZOP条目: {updated}')
    print(f'✓ HAZOP总数: {len(hazop_patterns)}')
    print()


def merge_event_matrix(pt_complete, extracted_data):
    """
    合并event_matrix

    PT_complete结构:
    risk_catalog.event_matrix = [
        {
            "analysis_unit_id": "PT_MAIN",
            "failure_id": "P_MF_0001_01",
            "scenario_id": "PT_SC_003",
            "description": "车辆在平坦路面起步...",
            "severity": 1,
            "severity_reason": "S1：发生后/前碰撞...",
            "exposure": 4,
            "exposure_reason": "E4:普通路面驾驶...",
            "controllability": 1,
            "controllability_reason": "C1:后车驾驶员可通过...",
            "expected_asil": "QM",
            "safety_goal": None,
            "safe_state": None,
            "ftti": None,
            "vehicle_hazard_id": "P_hzrd_01001"
        },
        ...
    ]
    """
    print('[4] 合并 risk_catalog.event_matrix')
    print('-' * 80)

    event_matrix = pt_complete['risk_catalog']['event_matrix']
    hara_events = extracted_data['hara_events']

    # 获取现有的最大scenario_id编号
    existing_scenario_ids = [e['scenario_id'] for e in event_matrix if e.get('scenario_id', '').startswith('PT_SC_')]
    max_scenario_num = 0
    for sid in existing_scenario_ids:
        try:
            num = int(sid.split('_')[-1])
            max_scenario_num = max(max_scenario_num, num)
        except:
            pass

    print(f'现有最大scenario_id: PT_SC_{max_scenario_num:03d}')

    added = 0
    next_scenario_num = max_scenario_num + 1

    for event in hara_events:
        # 转换为PT_complete格式
        new_event = {
            'analysis_unit_id': 'PT_MAIN',
            'failure_id': event['failure_id'],
            'scenario_id': f'PT_SC_{next_scenario_num:03d}',
            'description': event['description'] or '',
            'severity': event['severity'],
            'severity_reason': event['severity_reason'] or '',
            'exposure': event['exposure'],
            'exposure_reason': event['exposure_reason'] or '',
            'controllability': event['controllability'],
            'controllability_reason': event['controllability_reason'] or '',
            'expected_asil': event['asil'],
            'safety_goal': event['safety_goal'],
            'safe_state': event['safe_state'],
            'ftti': event['ftti'],
            'vehicle_hazard_id': event['hazard_id']
        }

        event_matrix.append(new_event)
        added += 1
        next_scenario_num += 1

    print(f'✓ 新增HARA事件: {added}')
    print(f'✓ 事件总数: {len(event_matrix)}')
    print(f'✓ 新scenario_id范围: PT_SC_{max_scenario_num+1:03d} ~ PT_SC_{next_scenario_num-1:03d}')
    print()


def merge_scenario_sets(pt_complete, extracted_data):
    """
    合并scenario_sets

    PT_complete结构:
    risk_catalog.scenario_sets = {
        "pt_main": {
            "PT_SC_001": "场景描述1",
            "PT_SC_002": "场景描述2",
            ...
        }
    }
    """
    print('[5] 合并 risk_catalog.scenario_sets')
    print('-' * 80)

    scenario_sets = pt_complete['risk_catalog']['scenario_sets']

    if 'pt_main' not in scenario_sets:
        scenario_sets['pt_main'] = {}

    pt_main_scenarios = scenario_sets['pt_main']

    # 获取现有的最大scenario_id编号
    existing_ids = [sid for sid in pt_main_scenarios.keys() if sid.startswith('PT_SC_')]
    max_num = 0
    for sid in existing_ids:
        try:
            num = int(sid.split('_')[-1])
            max_num = max(max_num, num)
        except:
            pass

    # 添加新场景
    hara_events = extracted_data['hara_events']
    added = 0
    next_num = max_num + 1

    for event in hara_events:
        scenario_id = f'PT_SC_{next_num:03d}'
        scenario_desc = event.get('scenario', '')

        if scenario_desc:
            pt_main_scenarios[scenario_id] = scenario_desc
            added += 1
            next_num += 1

    print(f'✓ 新增场景: {added}')
    print(f'✓ 场景总数: {len(pt_main_scenarios)}')
    print()


def update_safety_goal_catalog(pt_complete):
    """
    更新safety_goal_catalog，添加新的危害家族
    """
    print('[6] 更新 safety_goal_catalog.hazard_families')
    print('-' * 80)

    hazard_families = pt_complete['safety_goal_catalog']['hazard_families']

    if 'energy_recovery' not in hazard_families:
        hazard_families['energy_recovery'] = '能量回收相关危害'
        print('✓ 添加新危害家族: energy_recovery')
    else:
        print('⚠️  危害家族 energy_recovery 已存在')
    print()


def update_provenance(pt_complete, extracted_data):
    """更新数据来源信息"""
    print('[7] 更新 provenance (数据来源信息)')
    print('-' * 80)

    prov = pt_complete['provenance']

    # 更新计数
    if 'total_functions' in prov:
        prov['total_functions'] = len(pt_complete['function_catalog']['function_index'])

    if 'total_hazop_entries' in prov:
        prov['total_hazop_entries'] = len(pt_complete['analysis_catalog']['hazop_patterns'])

    if 'total_hara_events' in prov:
        prov['total_hara_events'] = len(pt_complete['risk_catalog']['event_matrix'])

    # 添加新的数据源
    if 'sources' not in prov:
        prov['sources'] = []

    prov['sources'].append({
        'name': extracted_data['source'],
        'function': extracted_data['function_name'],
        'date_added': '2026-08-27'
    })

    print(f'✓ 更新统计信息')
    print(f'  - 功能总数: {prov.get("total_functions", "N/A")}')
    print(f'  - HAZOP总数: {prov.get("total_hazop_entries", "N/A")}')
    print(f'  - HARA事件总数: {prov.get("total_hara_events", "N/A")}')
    print()


def main():
    """主函数"""
    print('=' * 80)
    print('将能量回收功能数据整合到PT_complete.json')
    print('=' * 80)
    print()

    # 1. 加载数据
    print('加载数据...')
    pt_complete_path = 'references/domain_packs/PT_complete.json'
    extracted_path = 'output/pt_energy_recovery_extracted.json'

    pt_complete = load_json(pt_complete_path)
    extracted_data = load_json(extracted_path)
    print()

    # 2. 分析提取的数据
    failure_ids, failure_modes_by_id = analyze_extracted_data(extracted_data)

    # 3. 逐步合并
    merge_function_index(pt_complete, extracted_data)
    merge_failure_mode_rules(pt_complete, extracted_data, failure_modes_by_id)
    merge_hazop_patterns(pt_complete, extracted_data)
    merge_event_matrix(pt_complete, extracted_data)
    merge_scenario_sets(pt_complete, extracted_data)
    update_safety_goal_catalog(pt_complete)
    update_provenance(pt_complete, extracted_data)

    # 4. 保存备份
    backup_path = 'references/domain_packs/PT_complete_backup_20260827.json'
    print(f'保存备份: {backup_path}')
    save_json(load_json(pt_complete_path), backup_path)
    print()

    # 5. 保存更新后的文件
    print(f'保存更新后的文件: {pt_complete_path}')
    save_json(pt_complete, pt_complete_path)
    print()

    print('=' * 80)
    print('✓ 整合完成！')
    print('=' * 80)


if __name__ == '__main__':
    main()
