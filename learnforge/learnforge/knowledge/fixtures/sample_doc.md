# Python GIL

全局解释器锁 GIL 保证同一时刻只有一个线程执行 Python 字节码。CPU 密集型任务受限于 GIL，常用多进程绕开。

## IO 密集型

IO 密集型任务在等待时会释放 GIL，因此多线程仍能提升吞吐。asyncio 用事件循环单线程并发处理大量 IO。
