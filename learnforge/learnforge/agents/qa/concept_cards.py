"""Local high-frequency concept cards for instant, deeper QA answers.

These cards are intentionally small and curated. They cover interview staples
where a remote LLM call is slower and often less concrete than a known-good
study answer.
"""

from __future__ import annotations

from typing import Optional


def answer_for(question: str) -> Optional[str]:
    q = (question or "").lower()
    compact = q.replace(" ", "")
    if "redis" in q and any(k in compact for k in ("持久化", "rdb", "aof", "快照", "混合持久化")):
        return _redis_persistence()
    if any(k in compact for k in ("rocketmq", "rockmq")) and any(
        k in compact for k in ("可靠", "不丢", "保证消息", "消息丢失", "一致性", "高可用")
    ):
        return _rocketmq_reliability()
    if any(k in compact for k in ("springboot", "springapplication")) and any(
        k in compact for k in ("启动", "run", "生命周期", "启动流程")
    ):
        return _spring_boot_startup()
    return None


def _redis_persistence() -> str:
    return """### 核心结论

Redis 的持久化不是“把缓存变成数据库”，而是在内存数据之外提供**重启恢复能力**。主机制有三类：

- **RDB**：某个时间点的全量快照，适合备份、灾备、快速恢复。
- **AOF**：追加记录写命令，适合降低数据丢失窗口。
- **混合持久化**：AOF 重写文件前半段用 RDB 快照，后半段追加增量命令，是 Redis 4.0+ 常见生产选择。

### RDB：快照机制

RDB 会在满足 `save` 条件、执行 `BGSAVE`、主从全量同步、正常关闭等场景下生成快照文件。核心流程是：

1. 主进程 `fork` 子进程。
2. 子进程把当前内存数据写成临时 RDB 文件。
3. 写完后原子替换旧的 `dump.rdb`。

它的关键点是 **copy-on-write**：fork 之后父进程继续处理请求，只有被修改的内存页才会复制，所以 RDB 通常不阻塞主流程，但 fork 本身和脏页复制会带来瞬时开销。

### AOF：追加日志机制

AOF 记录的是写命令，例如 `SET`、`HSET`、`DEL`。Redis 重启时会重放 AOF 来恢复数据。

常见 `appendfsync` 策略：

- `always`：每次写都刷盘，数据最安全，但吞吐最低。
- `everysec`：每秒刷盘，生产最常用，通常最多丢 1 秒数据。
- `no`：交给操作系统决定，性能好但崩溃丢失窗口不可控。

AOF 文件会越来越大，所以 Redis 会做 **AOF rewrite**：把当前数据状态压缩成更短的命令序列。比如一个 key 被写了 100 次，重写后只保留最终状态需要的命令。

### 混合持久化

开启 `aof-use-rdb-preamble yes` 后，AOF 重写生成的新文件会包含：

- 前半段：RDB 格式的全量快照。
- 后半段：重写期间发生的增量 AOF 命令。

这样恢复时既有 RDB 的加载速度，又保留 AOF 的较小数据丢失窗口。生产里如果开启 AOF，通常会优先考虑混合持久化。

### 场景与取舍

| 机制 | 优点 | 代价 | 适合场景 |
|---|---|---|---|
| RDB | 文件小、恢复快、便于备份 | 两次快照之间的数据可能丢失 | 缓存、可重建数据、定期备份 |
| AOF everysec | 数据丢失窗口小 | 文件更大，恢复较慢，写入有额外 I/O | 不能轻易丢写入的业务 |
| AOF always | 最强持久性 | 性能损耗明显 | 极少数强持久场景 |
| 混合持久化 | 恢复速度和安全性更均衡 | 配置与运维复杂度略高 | 大多数开启 AOF 的生产场景 |

### 生产建议

- 如果 Redis 只是纯缓存，数据可从 DB 重建：可以只开 RDB，甚至不开持久化，但要明确冷启动成本。
- 如果 Redis 承载分布式锁、计数、队列、会话等不可随便丢的数据：建议 AOF `everysec`，并开启混合持久化。
- 关注 `fork` 延迟、AOF rewrite、磁盘 I/O、`used_memory_rss`、`aof_pending_bio_fsync` 等指标。
- 不要把持久化当成高可用。RDB/AOF 解决“重启恢复”，主从、哨兵、Cluster 才解决节点故障切换。

### 常见误区

- **误区 1：RDB 是实时持久化。** 不是。它是快照，两次快照之间的写入可能丢。
- **误区 2：AOF 一定不丢数据。** 取决于 `appendfsync`。`everysec` 仍可能丢最近 1 秒。
- **误区 3：AOF 文件只会无限变大。** Redis 会通过 rewrite 压缩历史命令。
- **误区 4：开启持久化就等于数据安全。** 磁盘损坏、误删、主从复制延迟、配置错误仍需要备份和高可用方案。
- **误区 5：Redis 做缓存就不用管持久化。** 如果冷启动会打爆数据库，RDB 快照也很有价值。

### 面试追问

- `BGSAVE` 为什么通常不会长时间阻塞主线程？
- AOF rewrite 期间新写入的数据怎么处理？
- `appendfsync everysec` 最坏会丢多少数据？
- RDB 和 AOF 同时开启时 Redis 重启优先加载哪个？
- 混合持久化为什么能加快恢复？

### 面试口述版

Redis 持久化主要是 RDB 和 AOF。RDB 是快照，恢复快、文件紧凑，但会丢失两次快照之间的数据；AOF 是写命令日志，配 `everysec` 时通常最多丢 1 秒，但文件更大、恢复更慢，所以需要 rewrite。生产里常用 AOF everysec 加混合持久化，在恢复速度和数据安全之间做平衡。同时要注意，持久化只解决重启恢复，不等于高可用。
"""


def _rocketmq_reliability() -> str:
    return """### 核心结论

RocketMQ 的可靠性不能只说“持久化 + 主从复制”。面试里更好的回答是按**一条消息的生命周期**拆：

`Producer 发送可靠性 → Broker 存储可靠性 → Broker 高可用 → Consumer 消费可靠性 → 业务幂等兜底`

它的目标通常不是“绝对只投递一次”，而是工程上尽量做到：

- **不轻易丢消息**：发送确认、同步刷盘、主从复制、故障切换。
- **允许重复但可控**：消费失败重试、至少一次投递、业务幂等。
- **顺序和事务按场景保证**：顺序消息、事务消息解决特定一致性问题。

### Producer：发送端怎么避免丢

Producer 侧主要解决“消息有没有成功到达 Broker”的问题。

- **同步发送并检查结果**：关键业务不要 fire-and-forget，发送后检查 `SendResult`。
- **失败重试**：网络抖动、Broker 短暂不可用时，Producer 可重试或切换 Broker。
- **超时与异常处理**：发送超时不能简单认为成功或失败，要结合业务唯一键做补偿查询。
- **业务落库 + 发消息协调**：订单这类场景常见做法是先有业务状态，再用事务消息或本地消息表补偿，避免“业务成功但消息没发”。

这里的重点是：Producer 的可靠性不只是 SDK 重试，真正要和业务状态一起设计。

### Broker：存储端怎么避免丢

Broker 侧负责“消息到达后能不能安全落盘、宕机后能不能恢复”。

- **CommitLog 顺序写**：消息先写入 CommitLog，顺序写磁盘吞吐高。
- **ConsumeQueue 索引**：按 Topic/Queue 建消费索引，Consumer 通过队列位点消费。
- **刷盘策略**：
  - `SYNC_FLUSH`：Broker 等消息刷盘成功再返回，可靠性高，延迟更高。
  - `ASYNC_FLUSH`：先写 PageCache 就返回，性能高，但机器宕机可能丢最近一小段数据。
- **磁盘与 PageCache 风险**：只写入内存页缓存不等于真正落盘，所以强可靠场景要关注同步刷盘。

所以 Broker 存储可靠性的核心取舍是：同步刷盘更稳，异步刷盘更快。

### Broker 高可用：主从和故障切换

Broker 高可用解决“单台 Broker 挂了怎么办”。

- **主从复制**：Master 写入后复制给 Slave。
- **同步复制 vs 异步复制**：
  - 同步复制：等 Slave 确认后再返回，可靠性更高，延迟更大。
  - 异步复制：Master 返回更快，但 Master 宕机时可能丢未同步消息。
- **Dledger / Controller 模式**：用于自动选主，减少传统主从手动切换的问题。
- **NameServer 路由发现**：客户端通过 NameServer 感知 Broker 路由变化，但 NameServer 本身不存消息。

面试里要说清楚：主从复制解决的是 Broker 节点故障，不等于消费一定成功，也不等于业务幂等。

### Consumer：消费端怎么保证处理成功

Consumer 侧解决“消息拿到了，业务有没有真正处理成功”的问题。

- **消费确认 ACK**：消费成功才提交 offset；失败则返回失败或抛异常。
- **重试队列**：消费失败会进入重试，RocketMQ 会延迟后再次投递。
- **死信队列 DLQ**：超过最大重试次数后进入死信队列，方便人工排查或补偿。
- **消费进度 offset**：Broker 维护消费进度，Consumer 重启后从已提交位置继续。

这里要强调：RocketMQ 通常提供**至少一次**语义，所以 Consumer 可能重复收到消息。

### 幂等：为什么业务必须兜底

可靠消息系统一般会在“丢失”和“重复”之间优先避免丢失，因此重复投递是正常现象。

常见幂等方案：

- 用业务唯一键，例如 `orderId + eventType`。
- 建去重表或处理流水表。
- 用状态机限制状态流转，例如只能从 `PAID` 到 `DELIVERING`，不能重复扣减库存。
- 对外部调用设计幂等 token。

所以完整答案必须包含：RocketMQ 保证投递可靠，但业务侧要保证消费幂等。

### 事务消息：解决什么一致性问题

事务消息用于解决“本地事务成功，但消息发送失败”或“消息发出，但本地事务失败”的一致性问题。

典型流程：

1. Producer 发送 half message。
2. Broker 暂不投递给 Consumer。
3. Producer 执行业务本地事务。
4. 本地事务成功则 commit message，失败则 rollback。
5. 如果 Producer 掉线，Broker 会回查事务状态。

它解决的是本地事务和消息发送之间的一致性，不是替代分布式事务的万能方案。

### 顺序消息：可靠性里的另一个维度

如果业务要求同一订单的消息按顺序处理，需要：

- Producer 把同一业务 key 路由到同一个 MessageQueue。
- Consumer 对同一个队列串行消费。
- 消费失败时谨慎重试，否则可能阻塞后续消息。

顺序消息的代价是并发度下降，所以只应该对需要顺序的局部 key 使用。

### 常见配置取舍

| 目标 | 偏可靠配置 | 代价 |
|---|---|---|
| 避免 Producer 发送丢失 | 同步发送、失败重试、业务补偿 | 延迟更高，逻辑更复杂 |
| 避免 Broker 宕机丢失 | `SYNC_FLUSH` | 写入延迟增加 |
| 避免 Master 故障丢失 | 同步复制 / 多副本 | 吞吐下降 |
| 避免 Consumer 处理丢失 | 成功后 ACK、失败重试、DLQ | 可能重复消费 |
| 避免重复造成业务错误 | 幂等表、唯一键、状态机 | 业务侧要多做设计 |

### 常见误区

- **误区 1：Broker 持久化了就不会丢。** 异步刷盘时机器宕机仍可能丢 PageCache 中的数据。
- **误区 2：主从复制就万无一失。** 异步复制下 Master 宕机可能丢未同步到 Slave 的消息。
- **误区 3：消费成功等于业务成功。** 只有业务处理成功后再 ACK 才算真正成功。
- **误区 4：RocketMQ 能保证 exactly-once。** 更实际的说法是至少一次投递 + 业务幂等。
- **误区 5：事务消息解决所有分布式一致性。** 它主要解决本地事务和发消息的一致性。

### 面试口述版

RocketMQ 的可靠性我会按消息链路回答。Producer 侧用同步发送、发送结果确认、失败重试和业务补偿，保证消息尽量到达 Broker；Broker 侧通过 CommitLog 顺序写、刷盘策略、主从复制或 Dledger/Controller 高可用来降低存储和节点故障导致的丢失；Consumer 侧通过 ACK、offset、失败重试和死信队列保证处理失败可恢复。因为 RocketMQ 通常是至少一次投递，所以可能重复消费，最终一定要靠业务唯一键、去重表或状态机做幂等。强可靠场景会选择同步刷盘和同步复制，但代价是延迟和吞吐下降。
"""


def _spring_boot_startup() -> str:
    return """### 核心结论

如果问“Spring Boot 怎么启动”，面试里不要只说 `main()` 调 `SpringApplication.run()`。更完整的回答应该是：

`main 入口 → 创建 SpringApplication → 准备 Environment → 创建 ApplicationContext → refresh 容器 → 自动配置和 Bean 生命周期 → 启动内嵌 WebServer → 执行 Runner/发布事件`

一句话概括：`SpringApplication.run()` 是启动总入口，真正核心在于**准备运行环境、创建并刷新 Spring 容器、完成自动配置，最后启动 Web 容器并回调应用启动逻辑**。

### 入口：main 方法做了什么

典型入口是：

```java
@SpringBootApplication
public class DemoApplication {
    public static void main(String[] args) {
        SpringApplication.run(DemoApplication.class, args);
    }
}
```

这里的 `@SpringBootApplication` 不是一个普通标记，它组合了：

- `@SpringBootConfiguration`：说明这是一个配置类。
- `@EnableAutoConfiguration`：开启自动配置。
- `@ComponentScan`：从当前包及子包扫描组件。

所以 main 方法只是入口，真正启动逻辑在 `SpringApplication.run()` 内部。

### 第一步：创建 SpringApplication

`SpringApplication.run(primarySource, args)` 会先构造 `SpringApplication` 对象，主要做几件事：

- 推断应用类型：普通应用、Servlet Web 应用、Reactive Web 应用。
- 保存主配置类，也就是传入的启动类。
- 加载初始化器 `ApplicationContextInitializer`。
- 加载监听器 `ApplicationListener`。
- 推断 main 方法所在类。

这一步可以理解为：先决定“我要启动什么类型的 Spring 应用，以及启动时有哪些扩展点要参与”。

### 第二步：准备 Environment

接着 Spring Boot 会准备运行环境 `Environment`：

- 读取命令行参数。
- 加载 `application.properties` / `application.yml`。
- 处理 profile，例如 `dev`、`test`、`prod`。
- 合并系统环境变量、JVM 参数、配置文件等属性源。

这一步非常关键，因为后面的自动配置大量依赖条件判断，例如：

- classpath 上有没有某个类。
- 配置里有没有某个开关。
- 当前是不是 Web 环境。
- 容器里是否已经存在某个 Bean。

### 第三步：创建 ApplicationContext

Spring Boot 会根据应用类型创建不同的上下文：

- Servlet Web：`AnnotationConfigServletWebServerApplicationContext`
- Reactive Web：`AnnotationConfigReactiveWebServerApplicationContext`
- 非 Web：`AnnotationConfigApplicationContext`

这一步回答了一个面试常问点：Spring Boot 为什么能启动内嵌 Tomcat？因为 Web 场景下创建的是 WebServer 相关的 `ApplicationContext`。

### 第四步：refresh 容器，这是核心

`refresh()` 是 Spring 容器启动的核心阶段，很多重要动作都发生在这里：

1. 准备 BeanFactory。
2. 解析配置类和 BeanDefinition。
3. 执行 `BeanFactoryPostProcessor`，例如配置类解析、占位符处理。
4. 注册 `BeanPostProcessor`。
5. 初始化国际化、事件广播器等基础设施。
6. 实例化非懒加载单例 Bean。
7. 触发 Bean 生命周期回调，例如构造方法、依赖注入、`Aware`、`@PostConstruct`、`InitializingBean`、自定义 init 方法。

所以如果问“Spring Boot 启动核心是什么”，可以说核心仍然是 Spring 的 `ApplicationContext.refresh()`。

### 自动配置什么时候生效

自动配置来自 `@EnableAutoConfiguration`。

它会通过导入机制加载自动配置类，并结合条件注解判断是否生效，例如：

- `@ConditionalOnClass`
- `@ConditionalOnMissingBean`
- `@ConditionalOnProperty`
- `@ConditionalOnWebApplication`

比如你引入 `spring-boot-starter-web` 后，classpath 上有 Servlet、Tomcat、Spring MVC 相关类，自动配置就会创建 MVC、DispatcherServlet、内嵌 Tomcat 等相关 Bean。

### 内嵌 WebServer 什么时候启动

在 Servlet Web 应用里，`refresh()` 过程中会创建并启动内嵌 WebServer，例如 Tomcat。

大致过程是：

- 自动配置创建 `ServletWebServerFactory`，常见实现是 `TomcatServletWebServerFactory`。
- WebServer ApplicationContext 在刷新时调用工厂创建 WebServer。
- 注册 Servlet、Filter、Listener。
- 启动 Tomcat 并监听端口。

所以更准确地说：Spring Boot 不是“外部 Tomcat 部署 war”，而是通过自动配置和 WebServerFactory 在应用进程内启动嵌入式服务器。

### 启动后回调

容器刷新完成后，Spring Boot 还会执行启动完成后的扩展点：

- 发布启动相关事件，例如 `ApplicationStartedEvent`、`ApplicationReadyEvent`。
- 执行 `CommandLineRunner`。
- 执行 `ApplicationRunner`。

这些适合做启动后初始化，但要谨慎：如果里面逻辑很慢，会拉长应用 ready 时间；如果抛异常，可能导致应用启动失败。

### 常见误区

- **误区 1：Spring Boot 启动就是 main 方法。** main 只是入口，核心是 `SpringApplication.run()` 和 `ApplicationContext.refresh()`。
- **误区 2：自动配置就是无脑创建 Bean。** 自动配置大量依赖条件注解，已有 Bean 时通常会让用户配置优先。
- **误区 3：Spring Boot 必须是 Web 项目。** 它也可以启动非 Web 应用，只是创建的 ApplicationContext 不同。
- **误区 4：内嵌 Tomcat 是手动启动的。** 它是 Web 场景下自动配置和 WebServerFactory 共同完成的。
- **误区 5：Runner 适合放大量初始化逻辑。** 可以放，但要考虑启动耗时、失败影响和幂等性。

### 面试追问

- `@SpringBootApplication` 包含哪几个核心注解？
- `SpringApplication.run()` 大概做了哪些阶段？
- `ApplicationContext.refresh()` 为什么是核心？
- 自动配置如何做到“有条件地生效”？
- 内嵌 Tomcat 是什么时候启动的？
- `CommandLineRunner` 和 `ApplicationRunner` 的执行时机是什么？

### 面试口述版

Spring Boot 的启动入口是 main 方法调用 `SpringApplication.run()`，但真正流程要分阶段看。首先会创建 `SpringApplication`，推断应用类型并加载初始化器和监听器；然后准备 `Environment`，加载配置文件、profile、命令行参数；接着根据应用类型创建 `ApplicationContext`。核心阶段是 `refresh()`，它会解析 BeanDefinition、执行后置处理器、实例化单例 Bean，并触发 Bean 生命周期。自动配置通过 `@EnableAutoConfiguration` 和一系列 `@Conditional` 条件注解生效。对于 Web 应用，刷新上下文时还会通过 `ServletWebServerFactory` 创建并启动内嵌 Tomcat。最后执行 Runner 并发布启动完成事件。
"""
