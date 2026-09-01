"""
测试简化版 stage_hara 的 finalize 功能
"""
import sys
import json
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.stages.stage_hara_simple import finalize


def create_test_s3_hazop():
    """创建测试用的S3 HAZOP数据"""
    s3_data = {
        "domain": "PT",
        "entries": [
            {
                "func_id": "P_func_0001",
                "func_name": "档位控制及指示功能",
                "analysis_unit_id": "P_func_0001",
                "failure_mode": "丢失",
                "anomalies": [
                    {
                        "failure_id": "P_MF_0001_01",
                        "description": "P档切入D档位失效",
                        "hazards": [
                            {
                                "description": "驱动扭矩丢失"
                            }
                        ]
                    }
                ]
            },
            {
                "func_id": "P_func_0002",
                "func_name": "驱动扭矩控制",
                "analysis_unit_id": "P_func_0002",
                "failure_mode": "非预期",
                "anomalies": [
                    {
                        "failure_id": "P_MF_0002_01",
                        "description": "驱动扭矩非预期输出",
                        "hazards": [
                            {
                                "description": "车辆非预期加速"
                            }
                        ]
                    }
                ]
            },
            {
                "func_id": "P_func_9999",
                "func_name": "未知功能",
                "analysis_unit_id": "P_func_9999",
                "failure_mode": "未知模式",
                "anomalies": [
                    {
                        "failure_id": "P_MF_9999_01",
                        "description": "未知异常",
                        "hazards": [
                            {
                                "description": "未知危害"
                            }
                        ]
                    }
                ]
            }
        ]
    }

    # 保存测试文件
    test_file = Path("test_s3_hazop.json")
    with open(test_file, 'w', encoding='utf-8') as f:
        json.dump(s3_data, f, ensure_ascii=False, indent=2)

    print(f"创建测试S3文件: {test_file}")
    return str(test_file)


def test_finalize():
    """测试finalize功能"""
    print('=' * 80)
    print('测试简化版 stage_hara finalize')
    print('=' * 80)

    # 1. 创建测试数据
    s3_file = create_test_s3_hazop()
    output_file = "test_s4_hara_prefill_draft.json"

    try:
        # 2. 执行finalize
        result = finalize(s3_file, output_file)

        # 3. 验证结果
        print('\n' + '=' * 80)
        print('验证结果')
        print('=' * 80)

        # 检查基本结构
        assert result is not None, "结果为空"
        assert "domain" in result, "缺少domain字段"
        assert "hazards" in result, "缺少hazards字段"
        assert "statistics" in result, "缺少statistics字段"
        assert result.get("artifact_kind") == "s4_hara_prefill_draft", "产物类型错误"
        assert result.get("validation", {}).get("passed") is False, "草稿不得标记为验证通过"

        print(f"✓ 基本结构完整")

        # 检查危害组
        hazards = result["hazards"]
        assert len(hazards) == 3, f"危害组数量错误: {len(hazards)}"
        print(f"✓ 危害组数量正确: {len(hazards)}")

        # 检查第一个危害组（应该精确匹配）
        h1 = hazards[0]
        assert h1["func_id"] == "P_func_0001", "func_id错误"
        assert h1["failure_mode"] == "丢失", "failure_mode错误"
        assert h1["anomaly"] == "P档切入D档位失效", "anomaly错误"
        assert not h1["skip"], "不应该skip"
        assert len(h1["events"]) > 0, "events为空"

        event1 = h1["events"][0]
        assert "prefill_source" in event1, "缺少prefill_source"
        print(f"✓ 第一个危害组正确，预填类型: {event1.get('prefill_source')}")

        # 检查第三个危害组（应该无匹配）
        h3 = hazards[2]
        assert h3["func_id"] == "P_func_9999", "func_id错误"
        event3 = h3["events"][0]
        assert event3["prefill_source"] == "none", f"预填类型错误: {event3['prefill_source']}"
        print(f"✓ 无匹配案例的危害组正确")

        # 草稿阶段不得提前计算正式 ASIL 或分配正式 ID
        assert all(
            not e.get("hazard_id") and not e.get("safety_goal_id") and not e.get("asil")
            for h in hazards for e in h.get("events", [])
        ), "预填草稿不得包含正式 ASIL/hazard_id/safety_goal_id"
        print("✓ 草稿未提前生成正式 ASIL/ID")

        # 检查统计信息
        stats = result["statistics"]
        print(f"\n统计信息:")
        print(f"  总危害组: {stats['total_hazards']}")
        print(f"  跳过: {stats['skip_count']}")
        print(f"  活跃: {stats['active_hazards']}")
        print(f"  总事件: {stats['total_events']}")
        print(f"  exact候选组: {stats['exact_candidate_groups']}")
        print(f"  需工程确认组: {stats['review_required_groups']}")

        print('\n' + '=' * 80)
        print('✓ 所有测试通过！')
        print('=' * 80)

        # 清理测试文件
        Path(s3_file).unlink()
        Path(output_file).unlink()
        print(f"\n已清理测试文件")

    except Exception as e:
        print(f'\n✗ 测试失败: {e}')
        import traceback
        traceback.print_exc()

        # 清理测试文件
        if Path(s3_file).exists():
            Path(s3_file).unlink()
        if Path(output_file).exists():
            Path(output_file).unlink()

        sys.exit(1)


if __name__ == '__main__':
    test_finalize()
