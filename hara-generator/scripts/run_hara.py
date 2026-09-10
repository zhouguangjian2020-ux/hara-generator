# scripts/run_hara.py
# HARA 分析管道主入口 — 仅做 CLI 调度，业务逻辑全部在 stages/ 和 utils/ 中
#
# 用法:
#   python run_hara.py parse <输入.docx> [--domain PT] [-o intermediate.json]
#   python run_hara.py merge <intermediate.json> <agent_s1.json> [-o s1_decisions.json]
#   python run_hara.py s2 generate <intermediate.json> <s1.json> [-o s2_draft.json]
#   python run_hara.py s2 compose <intermediate.json> <s1.json> <s2_draft.json> <agent_s2.json> [-o s2_decisions.json]
#   python run_hara.py s2 validate <s1.json> <s2_decisions.json> [--intermediate intermediate.json]
#   python run_hara.py s3 generate <intermediate.json> <s1.json> <s2_decisions.json> [-o s3_draft.json]
#   python run_hara.py s3 compose <intermediate.json> <s1.json> <s2_decisions.json> <s3_draft.json> <agent_s3.json> [-o s3_hazop.json]
#   python run_hara.py s3 validate <s1.json> <s2_decisions.json> <s3_hazop.json> [--intermediate intermediate.json]
#   python run_hara.py write <intermediate.json> <s1.json> <s2.json> [--s3 s3.json] [--s4 s4.json] [-o output.xlsx]
#   python run_hara.py hara prepare <s3_hazop.json> --intermediate intermediate.json --s1 s1_decisions.json --s2 s2_decisions.json [-o s4_skeleton.json]
#   python run_hara.py hara validate <s4_hara.json> [-o s4_final.json] [--s3 s3_hazop.json]
#   python run_hara.py sg generate <s4_hara_final.json> [-o safety_goals.json]

import argparse
import sys
from pathlib import Path

# 强制 stdout/stderr 使用 UTF-8（兼容非 UTF-8 locale 的部署环境）
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

# 将 scripts/ 加入 path（确保 utils/ 和 stages/ 可导入）
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))


def main():
    parser = argparse.ArgumentParser(
        description="HARA 危害分析与风险评估管道",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", help="子命令")

    # parse
    p = sub.add_parser("parse", help="阶段一: 解析 DOCX/DOC → intermediate.json")
    p.add_argument("input", help="输入 .docx 或旧 .doc 文件路径")
    p.add_argument(
        "--domain",
        help="显式项目域（推荐；支持 PT/P、CS、CB、AD、ET、BD 等别名）",
    )
    p.add_argument("--output", "-o", help="输出 JSON 路径（默认 intermediate.json）")
    p.add_argument("--document-request-output", help="Agent 文档解析请求输出路径（仅质量门未通过时生成）")

    # document validate / compose
    p_document = sub.add_parser("document", help="异构文档 Agent 结构校验与合并")
    document_sub = p_document.add_subparsers(dest="document_subcommand", help="document 子命令")
    p_doc_validate = document_sub.add_parser("validate", help="校验 agent_document_structure_v1")
    p_doc_validate.add_argument("intermediate", help="parse 生成的 intermediate.json")
    p_doc_validate.add_argument("agent_document", help="Agent 文档结构 JSON")
    p_doc_validate.add_argument("--request", help="document_parse_request.json（可选）")
    p_doc_compose = document_sub.add_parser("compose", help="合并 Agent 文档结构并生成可继续 S1 的 intermediate")
    p_doc_compose.add_argument("intermediate", help="parse 生成的 intermediate.json")
    p_doc_compose.add_argument("agent_document", help="Agent 文档结构 JSON")
    p_doc_compose.add_argument("--request", help="document_parse_request.json（可选）")
    p_doc_compose.add_argument("--output", "-o", default="intermediate_composed.json", help="合并后的 intermediate")

    # merge
    p = sub.add_parser("merge", help="合并规则结果与 Agent S1 决策 → s1_decisions.json")
    p.add_argument("intermediate", help="intermediate.json 路径")
    p.add_argument("agent_s1", help="Agent 输出的 S1 决策 JSON")
    p.add_argument("--output", "-o", default="s1_decisions.json", help="合并后的输出路径")
    p.add_argument("--validate-only", action="store_true", help="严格验证 Agent JSON 格式及 intermediate 范围，不合并")

    # s2 generate / validate
    p_s2 = sub.add_parser("s2", help="S2 失效模式生成与校验")
    s2_sub = p_s2.add_subparsers(dest="s2_subcommand", help="S2 子命令")
    p_s2_gen = s2_sub.add_parser("generate", help="生成 exact 锁定项和 compatible 待确认项的 S2 受控草稿")
    p_s2_gen.add_argument("intermediate", help="intermediate.json 路径")
    p_s2_gen.add_argument("s1_decisions", help="合并后的 s1_decisions.json 路径")
    p_s2_gen.add_argument("--output", "-o", default="s2_draft.json", help="输出受控草稿路径（不是最终 S2）")
    p_s2_compose = s2_sub.add_parser("compose", help="合并受控 S2 草稿与 Agent 补充，生成最终 S2")
    p_s2_compose.add_argument("intermediate", help="intermediate.json 路径")
    p_s2_compose.add_argument("s1_decisions", help="合并后的 s1_decisions.json 路径")
    p_s2_compose.add_argument("draft", help="s2 generate 输出的 s2_draft.json")
    p_s2_compose.add_argument("agent_s2", help="Agent 的 S2 补充/compatible 确认 JSON")
    p_s2_compose.add_argument("--output", "-o", default="s2_decisions.json", help="最终 s2_decisions.json 路径")
    p_s2_val = s2_sub.add_parser("validate", help="验证最终 S2 的范围、锁定项及 compatible 合同")
    p_s2_val.add_argument("s1_decisions", help="合并后的 s1_decisions.json 路径")
    p_s2_val.add_argument("s2_decisions", help="s2_decisions.json 路径")
    p_s2_val.add_argument("--intermediate", help="intermediate.json 路径（提供后同时校验 Domain Pack 锁定项）")

    # s3 generate / validate
    p_s3 = sub.add_parser("s3", help="S3 HAZOP 生成与校验")
    s3_sub = p_s3.add_subparsers(dest="s3_subcommand", help="S3 子命令")
    p_s3_gen = s3_sub.add_parser("generate", help="生成 exact 锁定 HAZOP 的 S3 受控草稿")
    p_s3_gen.add_argument("intermediate", help="intermediate.json 路径")
    p_s3_gen.add_argument("s1_decisions", help="合并后的 s1_decisions.json 路径")
    p_s3_gen.add_argument("s2_decisions", help="已验证的 s2_decisions.json 路径")
    p_s3_gen.add_argument("--output", "-o", default="s3_draft.json", help="输出受控草稿路径（不是最终 S3）")
    p_s3_compose = s3_sub.add_parser("compose", help="合并受控 S3 草稿与 Agent HAZOP，生成最终 S3")
    p_s3_compose.add_argument("intermediate", help="intermediate.json 路径")
    p_s3_compose.add_argument("s1_decisions", help="合并后的 s1_decisions.json 路径")
    p_s3_compose.add_argument("s2_decisions", help="已验证的 s2_decisions.json 路径")
    p_s3_compose.add_argument("draft", help="s3 generate 输出的 s3_draft.json")
    p_s3_compose.add_argument("agent_s3", help="Agent 的 S3 补充 JSON")
    p_s3_compose.add_argument("--output", "-o", default="s3_hazop.json", help="最终 s3_hazop.json 路径")
    p_s3_val = s3_sub.add_parser("validate", help="验证最终 S3 的范围、模式覆盖、compatible 追溯和 Pack 锁定项")
    p_s3_val.add_argument("s1_decisions", help="合并后的 s1_decisions.json 路径")
    p_s3_val.add_argument("s2_decisions", help="已验证的 s2_decisions.json 路径")
    p_s3_val.add_argument("s3_hazop", help="s3_hazop.json 路径")
    p_s3_val.add_argument("--intermediate", help="intermediate.json 路径（提供后同时校验 Domain Pack 锁定项）")

    # write
    p = sub.add_parser("write", help="JSON 决策 → Excel（可选 --s3/--s4 写入 HAZOP/HARA）")
    p.add_argument("intermediate", help="intermediate.json 路径")
    p.add_argument("s1_decisions", help="s1_decisions.json 路径")
    p.add_argument("s2_decisions", help="s2_decisions.json 路径")
    p.add_argument("--output", "-o", help="输出 Excel 路径（默认 output/result.xlsx）")
    p.add_argument("--template", "-t", help="Excel 模板路径（可选）")
    p.add_argument("--s3", help="s3_hazop.json 路径（可选，写入 HAZOP 分析表）")
    p.add_argument("--s4", help="s4_hara_final.json 路径（可选，写入 HARA 分析表并回填 HAZOP G 列）")
    p.add_argument("--s5", help="safety_goals.json 路径（可选，写入整车安全目标 sheet）")
    p.add_argument("--force", action="store_true", help="仅绕过非阶段边界的写入校验；S1/S2/S3/S4 硬校验不能绕过")

    # hara (S4: HAZOP → HARA)
    p_hara = sub.add_parser("hara", help="S4: HAZOP → HARA（prepare/validate）")
    hara_sub = p_hara.add_subparsers(dest="hara_subcommand", help="HARA 子命令")

    # hara prepare
    p_prep = hara_sub.add_parser("prepare", help="从 s3_hazop.json 生成 s4 骨架（Agent 填写 events）")
    p_prep.add_argument("s3_hazop", help="s3_hazop.json 路径")
    p_prep.add_argument("--output", "-o", default="s4_hara_skeleton.json", help="输出骨架 JSON 路径")
    p_prep.add_argument("--intermediate", required=True, help="intermediate.json 路径（必需，验证 Domain Pack 锁定的最终 S1 合同）")
    p_prep.add_argument("--s1", required=True, help="s1_decisions.json 路径（必需，用于 S1→S2/S3 硬边界检查）")
    p_prep.add_argument("--s2", required=True, help="s2_decisions.json 路径（必需，验证 S2 范围并检查 S2→S3 失效模式覆盖）")

    # hara autofill
    p_autofill = hara_sub.add_parser(
        "autofill",
        help="仅补齐 S4 事件的确定性追溯字段；不代替工程判断",
    )
    p_autofill.add_argument("s4_hara", help="Agent 填写的 s4_hara.json 路径")
    p_autofill.add_argument(
        "--output", "-o", default="s4_hara_autofilled.json",
        help="补齐后的 S4 草稿路径（默认不覆盖输入）",
    )

    # hara validate
    p_val = hara_sub.add_parser("validate", help="验证 Agent 填写的 s4_hara.json，计算 ASIL/ID")
    p_val.add_argument("s4_hara", help="Agent 填写的 s4_hara.json 路径")
    p_val.add_argument("--output", "-o", default="s4_hara_final.json", help="输出 final JSON 路径")
    p_val.add_argument("--s3", help="s3_hazop.json 路径（可选，用于引用完整性检查）")

    # hara finalize (新增：简化版，单一命令完成所有工作)
    p_final = hara_sub.add_parser("finalize", help="兼容命令：从 S3 生成案例库语义预填草稿；正式 final 仍需 hara validate")
    p_final.add_argument("s3_hazop", help="s3_hazop.json 路径")
    p_final.add_argument("--output", "-o", default="s4_hara_prefill_draft.json", help="输出预填草稿 JSON 路径")

    # sg (S5: 整车安全目标汇总)
    p_sg = sub.add_parser("sg", help="S5: 从 s4_hara_final 生成整车安全目标汇总")
    sg_sub = p_sg.add_subparsers(dest="sg_subcommand", help="安全目标子命令")

    # sg generate
    p_sg_gen = sg_sub.add_parser("generate", help="从 s4_hara_final.json 提取、去重、编号安全目标")
    p_sg_gen.add_argument("s4_hara_final", help="s4_hara_final.json 路径（validate 后的最终结果）")
    p_sg_gen.add_argument("--output", "-o", default="safety_goals.json", help="输出 safety_goals.json 路径")

    args = parser.parse_args()

    if args.command == "parse":
        from stages.stage_parse import run
    elif args.command == "document":
        from stages.stage_document import run
    elif args.command == "merge":
        from stages.stage_merge import run
    elif args.command == "s2":
        from stages.stage_s2 import run
    elif args.command == "s3":
        from stages.stage_s3 import run
    elif args.command == "write":
        from stages.stage_write import run
    elif args.command == "hara":
        # 根据子命令选择不同的处理模块
        subcmd = getattr(args, "hara_subcommand", None)
        if subcmd == "finalize":
            from stages.stage_hara_simple import run
        else:
            from stages.stage_hara import run
    elif args.command == "sg":
        from stages.stage_safety_goal import run
    else:
        parser.print_help()
        return

    run(args)


if __name__ == "__main__":
    main()
