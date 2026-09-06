# 参考数据来源与许可(重要:勿删,版权合规依据)

本项目是个人学习用途的复刻,不公开分发、不商用。以下参考数据均已在设计文档「业界调研」一节注明用途。

## tacthgin/ —— 50 层完整数据 + 系统实现参考

- 来源:<https://github.com/tacthgin/MagicTower>(MIT License,© 2021 陈起慧)
- `TiledMap/0.tmx ~ 50.tmx`:全部楼层地图(Tiled 格式,11×11,分层 CSV)
- `Json/`:monster / prop / door / npc / event / global 六张数据表
- `*.ts`(CalculateSystem / MonsterFightSystem / DamageSystem / UsePropSystem / GameEventSystem):战斗、伤害、道具、事件系统的 TypeScript 实现,作为"纯函数战斗"的架构参考
- 用途:由 `scripts/convert_tacthgin.py` 一次性转换为本项目 `game/data/` 下的 JSON
- 注意:该仓库怪物数值存在 3 处金币抄录差异,转换时以原版 TSW.exe 权威数值表(设计文档附录 A)为准校正

## tswKai/ —— 原版逆向参考

- 来源:<https://github.com/Z-H-Sun/tswKai>(MIT License)
- `monsters.rb`:从原版 TSW.exe 数据段提取的 33 怪权威数值(设计文档附录 A 的依据)
- `altar_math.md`:祭坛价格公式的逆向分析
- `connectivity.rb`:原版地图/楼梯连接结构参考

## 版权口径(设计文档 §3.1 有完整分析)

- 原版游戏(Tower of the Sorcerer)为自由软件(Free Soft),无书面许可
- MIT 许可覆盖以上仓库的代码与代码性数据,使用需保留本出处说明
- 地图布局/对白文本仍是原作关卡设计的复制 → **仅限个人学习,不公开分发、不商用**
- 本项目的美术素材全部程序自绘,不使用任何外部图片素材
