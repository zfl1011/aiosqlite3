"""
生成异步连接对象
"""
import concurrent
import asyncio
import sqlite3
from functools import partial
from .utils import (
    _ContextManager,
    _LazyloadContextManager,
    delegate_to_executor,
    proxy_property_directly
)
from .cursor import Cursor
from .log import logger


@delegate_to_executor(
    '_conn',
    (
        'commit',
        'rollback',
        'create_function',
        'create_aggregate',
        'create_collation',
        'interrupt',
        'set_authorizer',
        'set_progress_handler',
        'set_trace_callback',
        'enable_load_extension',
        'load_extension',
        'iterdump'
    )
)
@proxy_property_directly(
    '_conn',
    (
        'in_transaction',
        'total_changes'
    )
)
class Connection:
    def __init__(
            self,
            database,
            loop=None,
            executor=None,
            timeout=5,
            echo=False,
            check_same_thread=False,
            isolation_level='',
            **kwargs
    ):
        if check_same_thread:
            raise ValueError(
                'check_same_thread not is False -> %s'
                % check_same_thread
            )
        self._database = database
        self._loop = loop or asyncio.get_event_loop()
        self._kwargs = kwargs
        self._executor = executor
        self._echo = echo
        self._timeout = timeout
        self._isolation_level = isolation_level
        self._check_same_thread = check_same_thread
        self._conn = None

    def __enter__(self):
        """
        普通上下文处理
        """
        return self

    @asyncio.coroutine
    def __exit__(self, exc_type, exc, tbs):
        """
        普通上下文处理
        """
        yield from self.close()

    def _execute(self, func, *args, **kwargs):
        """
        把同步转为async运行
        """
        func = partial(func, *args, **kwargs)
        future = self._loop.run_in_executor(self._executor, func)
        return future

    @asyncio.coroutine
    def _connect(self):
        """
        async连接，必须使用多线程模式
        """
        func = self._execute(
            sqlite3.connect,
            self._database,
            timeout=self._timeout,
            isolation_level=self._isolation_level,
            check_same_thread=self._check_same_thread,
            **self._kwargs
        )
        self._conn = yield from func
        if self._echo:
            logger.debug('connect-> "%s" ok', self._database)


    @property
    def echo(self):
        return self._echo

    @property
    def loop(self):
        """
        连接使用的loop
        """
        return self._loop

    @property
    def timeout(self):
        """
        超时时间
        """
        return self._timeout

    @property
    def closed(self):
        """
        是否已关闭连接
        """
        if self._conn:
            return False
        return True

    @property
    def autocommit(self):
        """
        是否为自动commit
        """
        return self._conn.isolation_level is None

    @property
    def isolation_level(self):
        """
        智能,自动commit
        """
        return self._conn.isolation_level

    @isolation_level.setter
    def isolation_level(self, value: str) -> None:
        self._conn.isolation_level = value

    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._conn.row_factory = value

    @property
    def text_factory(self):
        return self._conn.text_factory

    @text_factory.setter
    def text_factory(self, value):
        self._conn.text_factory = value

    # @asyncio.coroutine
    # def _cursor(self):
    #     """
    #     获取异步代理cursor对象
    #     """
    #     cursor = yield from self._execute(self._conn.cursor)
    #     return self._create_cursor(cursor)

    def _create_cursor(self, cursor):
        """
        创建代理cursor
        """
        return Cursor(cursor, self, self._echo)

    def _create_context_cursor(self, coro):
        """
        创建支持await上下文cursor
        """
        return _LazyloadContextManager(coro, self._create_cursor)

    def cursor(self):
        """
        转换为上下文模式
        """
        coro = self._execute(self._conn.cursor)
        return self._create_context_cursor(coro)

    @asyncio.coroutine
    def close(self):
        """
        关闭
        """
        if not self._conn:
            return
        res = yield from self._execute(self._conn.close)
        self._conn = None
        if self._echo:
            logger.debug('close-> "%s" ok', self._database)
        return res

    def execute(
            self,
            sql,
            parameters=None,
    ):
        """
        Helper to create a cursor and execute the given query.
        """
        if self._echo:
            logger.info(
                'connection.execute->\n  sql: %s\n  args: %s',
                sql,
                str(parameters)
            )
        if parameters is None:
            parameters = []
        coro = self._execute(self._conn.execute, sql, parameters)
        return self._create_context_cursor(coro)

    def executemany(
            self,
            sql,
            parameters,
    ):
        """
        Helper to create a cursor and execute the given multiquery.
        """
        if self._echo:
            logger.info(
                'connection.executemany->\n  sql: %s\n  args: %s',
                sql,
                str(parameters)
            )
        coro = self._execute(
            self._conn.executemany,
            sql,
            parameters
        )
        return self._create_context_cursor(coro)

    def executescript(
            self,
            sql_script,
    ):
        """
        Helper to create a cursor and execute a user script.
        """
        if self._echo:
            logger.info(
                'connection.executescript->\n  sql_script: %s',
                sql_script
            )
        coro = yield from self._execute(
            self._conn.executescript,
            sql_script
        )
        return self._create_context_cursor(coro)


def connect(
        database: str,
        loop: asyncio.BaseEventLoop=None,
        executor: concurrent.futures.Executor=None,
        timeout: int = 5,
        echo: bool = False,
        isolation_level='',
        check_same_thread: bool = False,
        **kwargs: dict
):
    """
    把async方法执行后的对象创建为async上下文模式
    """
    coro = _connect(
        database,
        loop=loop,
        executor=executor,
        timeout=timeout,
        echo=echo,
        isolation_level=isolation_level,
        check_same_thread=check_same_thread,
        **kwargs
    )
    return _ContextManager(coro)


@asyncio.coroutine
def _connect(
        database: str,
        loop: asyncio.BaseEventLoop=None,
        executor: concurrent.futures.Executor=None,
        timeout: int = 5,
        echo: bool = False,
        isolation_level='',
        check_same_thread: bool = False,
        **kwargs: dict
):
    """
    async 方法代理
    """
    if loop is None:
        loop = asyncio.get_event_loop()
    conn = Connection(
        database,
        loop=loop,
        executor=executor,
        timeout=timeout,
        echo=echo,
        isolation_level=isolation_level,
        check_same_thread=check_same_thread,
        **kwargs
    )
    yield from conn._connect()
    return conn
