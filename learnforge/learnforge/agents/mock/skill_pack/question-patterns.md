# 技术面试高频题库（vendored reference）

> **来源**：tech-interview-skill — <https://github.com/Lntanohuang/tech-interview-skill>
> （`tech-interview/references/question-patterns.md`）。上游整理自 CyC2018/CS-Notes、
> Snailclimb/JavaGuide、haizlin/fe-interview、xiaolincoding（小林coding）、doocs/advanced-java 等。
>
> 本文件为**人类参考**；运行时用的离线兜底题/few-shot 子集已蒸馏进
> `learnforge/agents/mock/interview_skill.py` 的 `QUESTION_PATTERNS`。归属见 `NOTICE.md`。

## 一、前端
1. JavaScript 事件循环（宏任务/微任务）— 中级
2. Vue 响应式原理 / Vue2 vs Vue3 — 中级
3. React setState 同步异步 / React 18 Automatic Batching — 中级
4. 浏览器输入 URL 到渲染完成的步骤 — 中级
5. 闭包：场景与内存问题 — 初级
6. CSS BFC：触发与作用 — 初级
7. React Fiber 架构解决什么问题 — 高级
8. 前端性能优化（网络/渲染/代码层）— 中级
9. Promise 原理与 all/race/allSettled — 中级
10. 跨域产生与解决方案 — 初级

## 二、后端
1. Java HashMap 底层 / JDK 1.8 优化 — 中级
2. Go GMP 调度模型 — 高级
3. Spring Boot 自动配置原理 — 中级
4. RESTful API 设计 — 初级
5. 微服务通信与分布式事务 — 高级
6. JVM GC / G1 vs ZGC — 高级
7. Python GIL 与并行 — 中级
8. 限流算法 — 中级
9. Go channel vs mutex 适用场景 — 中级
10. 幂等接口设计 — 中级

## 三、数据库
1. MySQL 索引为何用 B+ 树 — 中级
2. 事务隔离级别与 MVCC — 高级
3. Redis 为什么快 / 单线程模型 — 中级
4. MySQL 慢查询排查与优化 — 中级
5. Redis RDB vs AOF — 中级
6. Redis 穿透/击穿/雪崩 — 中级
7. MySQL 锁机制与死锁排查 — 高级
8. MongoDB 适用场景 — 中级
9. 分库分表方案与中间件 — 高级
10. Redis 与 MySQL 一致性 — 高级

## 四、系统设计
短链接 / 分布式限流 / 消息队列 / 秒杀 / 分布式 ID / 高可用 / 分布式缓存 / 实时通知 /
CAP·BASE 取舍 / Feed 流。（难度 中级~高级）

## 五、计算机基础
TCP 握手挥手 / 进程线程协程 / HTTP 各版本 / 排序复杂度与快排 / 死锁 / TCP vs UDP /
一致性哈希 / 虚拟内存 / LRU 实现 / HTTPS 与 TLS 握手。（难度 初级~高级）

## 难度校准参考
| 资历 | 基础题 | 项目追问 | 设计题 |
|------|--------|----------|--------|
| 1-2 年 | 初级 | 实现细节 | 简单编码 |
| 3-5 年 | 中级+原理对比 | 架构决策+权衡 | 单模块系统设计 |
| 5-8 年 | 高级+边界场景 | 全局视角+推动力 | 完整系统设计 |
| 8+ 年 | 架构哲学 | 技术战略+团队 | 复杂分布式系统 |
