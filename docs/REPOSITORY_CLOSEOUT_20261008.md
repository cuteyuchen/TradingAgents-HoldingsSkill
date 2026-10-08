# 仓库收拢记录（2026-10-08）

本轮将已批准的持仓优化和 V3 主线工作合并为 `codex/holdings-optimization-completion`，统一向 `main` 提交 PR。旧 PR #3、#4、#5、#6、#7、#8、#9、#18 的全部 HEAD 均已包含，按被替代关闭，不伪造为已合并。已合并的 PR 保持原状态。

## 保留与归档原则

- 保留 `main`、统一集成分支及两个仍绑定工作树的分支：`codex/phase-h-daily-workbench`、`codex/phase-l-historical-data-foundation`。
- 所有本地及远程 refs 已保存于 `output/completion-20261008/before-cleanup.bundle`，`git bundle verify` 确认完整历史。原工作区 patch 与分支/PR 清单也保留在该目录；诊断和数据库不提交。
- 下表独立历史先推送指定归档 tag，确认远程 SHA 后才退休分支。其余退休分支的提交均可从集成分支恢复。归档旧实现不表示采纳或合并这些独立提交。
- 保留其他工作树及未跟踪诊断文件。不重置、不清理工作区、不变基、不重写远程历史。

## 整理前完整分支清单

`独立提交` 是相对本轮集成 HEAD 的提交数；清理前再次核对远程 SHA。KEEP 为保留，INTEGRATED 为已包含后退休，ARCHIVE 为归档后退休。

| 位置 | 分支 | 原 HEAD | 独立提交 | 处置 / 恢复 tag |
| --- | --- | --- | ---: | --- |
| 本地 | `codex/holdings-optimization-completion` | `466abc623847` | 0 | KEEP |
| 本地 | `codex/phase-b-market-data-foundation` | `10d703eaa44a` | 0 | INTEGRATED |
| 本地 | `codex/phase-c-market-engine` | `9ac1efed489a` | 0 | INTEGRATED |
| 本地 | `codex/phase-d-realtime-monitor` | `7e07396e0bb9` | 0 | INTEGRATED |
| 本地 | `codex/phase-e-portfolio-engine` | `65522753237a` | 0 | INTEGRATED |
| 本地 | `codex/phase-g-alpha-memory` | `e18e4adda6ed` | 0 | INTEGRATED |
| 本地 | `codex/phase-h-daily-workbench` | `16b8afbe6d88` | 0 | KEEP |
| 本地 | `codex/phase-i-1-integrity-hardening` | `b92c3452f357` | 0 | INTEGRATED |
| 本地 | `codex/phase-i-backtest-calibration` | `59957bf3efdb` | 0 | INTEGRATED |
| 本地 | `codex/phase-i-decision-evaluation` | `76f4fbcf4fa4` | 2 | ARCHIVE；`archive/20261008/phase-i-decision-evaluation` |
| 本地 | `codex/phase-j-forward-observation` | `e93755b733c4` | 4 | ARCHIVE；`archive/20261008/phase-j-forward-observation` |
| 本地 | `codex/phase-j-parameter-governance` | `40c41376eed4` | 0 | INTEGRATED |
| 本地 | `codex/phase-k-production-readiness` | `ac7500e6e338` | 0 | INTEGRATED |
| 本地 | `codex/phase-l-historical-data-foundation` | `1c004badf1ff` | 0 | KEEP |
| 本地 | `codex/phase-m-pit-deterministic-recompute` | `208936d8ec97` | 0 | INTEGRATED |
| 本地 | `codex/phase-n-live-shadow-validation` | `3d68a777ebfd` | 0 | INTEGRATED |
| 本地 | `codex/phase-o-frontend-productization` | `c7b005901c71` | 0 | INTEGRATED |
| 本地 | `codex/v3-core-1-workflow-audit-foundation` | `80213944da34` | 0 | INTEGRATED |
| 本地 | `codex/v3-core-2-node-executor-resume` | `42b6fe156042` | 0 | INTEGRATED |
| 本地 | `codex/v3-core-3-local-verified-c9e0d89` | `c9e0d89774cd` | 1 | ARCHIVE；`archive/20261008/v3-core-3-local-verified` |
| 本地 | `codex/v3-core-3-true-multi-agent-workflow` | `32bdbcc28db5` | 0 | INTEGRATED |
| 本地 | `codex/v3-core-3-true-multi-agent-workflow-clean-20260907` | `32bdbcc28db5` | 0 | INTEGRATED |
| 本地 | `codex/v3-core-4-artifact-export` | `aa99cade3723` | 0 | INTEGRATED |
| 本地 | `codex/v3-market-1-market-session-systemic-risk` | `05f343531e27` | 0 | INTEGRATED |
| 本地 | `codex/v3-market-2-1-live-provider-hardening` | `1ef7adf470c8` | 0 | INTEGRATED |
| 本地 | `codex/v3-market-2-1-live-provider-hardening-prebase` | `cd9e979eda34` | 2 | ARCHIVE；`archive/20261008/market-provider-prebase` |
| 本地 | `codex/v3-market-2-unified-instrument-facade` | `4aa91d87ad94` | 0 | INTEGRATED |
| 本地 | `codex/v3-ui-0-quasar-foundation` | `1ef7adf470c8` | 0 | INTEGRATED |
| 本地 | `codex/v3-ui-3-holdings-workstation` | `26f3e7213005` | 0 | INTEGRATED |
| 本地 | `main` | `a1e12087eaf9` | 0 | KEEP |
| 远程 | `codex/fix-holdings-skill` | `8dacd09dfd9c` | 0 | INTEGRATED |
| 远程 | `codex/phase-b-market-data-foundation` | `10d703eaa44a` | 0 | INTEGRATED |
| 远程 | `codex/phase-c-market-engine` | `9ac1efed489a` | 0 | INTEGRATED |
| 远程 | `codex/phase-d-realtime-monitor` | `7e07396e0bb9` | 0 | INTEGRATED |
| 远程 | `codex/phase-e-portfolio-engine` | `65522753237a` | 0 | INTEGRATED |
| 远程 | `codex/phase-g-alpha-memory` | `e18e4adda6ed` | 0 | INTEGRATED |
| 远程 | `codex/phase-h-daily-workbench` | `16b8afbe6d88` | 0 | KEEP |
| 远程 | `codex/phase-i-1-integrity-hardening` | `b92c3452f357` | 0 | INTEGRATED |
| 远程 | `codex/phase-i-backtest-calibration` | `59957bf3efdb` | 0 | INTEGRATED |
| 远程 | `codex/phase-i-decision-evaluation` | `76f4fbcf4fa4` | 2 | ARCHIVE；`archive/20261008/phase-i-decision-evaluation` |
| 远程 | `codex/phase-j-forward-observation` | `e93755b733c4` | 4 | ARCHIVE；`archive/20261008/phase-j-forward-observation` |
| 远程 | `codex/phase-j-parameter-governance` | `40c41376eed4` | 0 | INTEGRATED |
| 远程 | `codex/phase-k-production-readiness` | `ac7500e6e338` | 0 | INTEGRATED |
| 远程 | `codex/phase-l-historical-data-foundation` | `1c004badf1ff` | 0 | KEEP |
| 远程 | `codex/phase-m-pit-deterministic-recompute` | `208936d8ec97` | 0 | INTEGRATED |
| 远程 | `codex/phase-n-live-shadow-validation` | `3d68a777ebfd` | 0 | INTEGRATED |
| 远程 | `codex/phase-o-frontend-productization` | `c7b005901c71` | 0 | INTEGRATED |
| 远程 | `codex/v3-core-1-workflow-audit-foundation` | `80213944da34` | 0 | INTEGRATED |
| 远程 | `codex/v3-core-2-node-executor-resume` | `b85ba486b79e` | 0 | INTEGRATED |
| 远程 | `codex/v3-core-3-true-multi-agent-workflow` | `32bdbcc28db5` | 0 | INTEGRATED |
| 远程 | `codex/v3-core-4-artifact-export` | `aa99cade3723` | 0 | INTEGRATED |
| 远程 | `codex/v3-market-1-market-session-systemic-risk` | `05f343531e27` | 0 | INTEGRATED |
| 远程 | `codex/v3-market-2-1-live-provider-hardening` | `1ef7adf470c8` | 0 | INTEGRATED |
| 远程 | `codex/v3-market-2-unified-instrument-facade` | `4aa91d87ad94` | 0 | INTEGRATED |
| 远程 | `codex/v3-ui-0-quasar-foundation` | `c6a6f8955ca9` | 0 | INTEGRATED |
| 远程 | `codex/v3-ui-1-unified-instrument-detail` | `dbd7af160332` | 0 | INTEGRATED |
| 远程 | `codex/v3-ui-2-dashboard` | `729a96b42e24` | 0 | INTEGRATED |
| 远程 | `codex/v3-ui-3-holdings-workstation` | `f3f23d89c4c9` | 0 | INTEGRATED |
| 远程 | `feature/v2-foundation` | `8baab6dcf5fd` | 2 | ARCHIVE；`archive/20261008/v2-foundation` |
| 远程 | `main` | `a1e12087eaf9` | 0 | KEEP |

## 验证及验收边界

本地最终后端、浏览器结果和当前 HEAD 的 CI 在统一 PR 中记录；CI 要求 backend、frontend、frontend-acceptance、docker 四项全部通过。先前 PR #18 的审查明确要求全套验收、新提交四项 CI 全绿且不要自动合并，本轮保留该最终合并确认。

`scripts/holdings_acceptance_status.py` 对默认用户库进行 SQLite `mode=ro` 检查：当前可证明的完整观察日 0/10，学习假设、检验、引用均为 0。P5 为 PENDING，真实账户对账、正常供应商数据、盘中操作、至少十个交易日和用户日常使用验收仍需真实证据。

对照学习验证为冻结证据的公开决策回放，指标是门控数量的未计成本毛贡献代理，不是实际账户收益、完整多 Agent A/B 或因果证明。本轮没有迁移、造数或部署真实用户库。

## 恢复方式

已包含分支可从下表原 SHA 创建；独立分支可用 `git switch -c <恢复分支> <归档tag>` 恢复。离线完整备份可用 `git clone output/completion-20261008/before-cleanup.bundle <新目录>` 检查。两个现有工作树继续保留，不受分支收拢影响。
