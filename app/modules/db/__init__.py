from typing import List
import time
import json
import threading
import os
import logging
import pyodbc

handler = logging.FileHandler("ms_connection.log")
formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
handler.setFormatter(formatter)
l = logging.getLogger(__name__)
l.addHandler(handler)
l.setLevel(logging.DEBUG)

cur_dir = os.path.dirname(os.path.realpath(__file__))

def repr_single(s):
    return "'" + repr('"' + s)[2:] if type(s) is str else str(s)

def in_thread(name):
    """Create thread with name"""
    def decorator(func):
        def wrapper(self, *args):
            self.threads[name] = dict()
            self.threads[name]['thread'] = \
                threading.Thread(target=func, args=(self, *args))
            self.threads[name]['running'] = True
            self.threads[name]['thread'].start()
        return wrapper
    return decorator

def default_if_lost(default):
    """Returns data if connection is open or default value if it is closed"""
    def decorator(func):
        def wrapper(self, *args, **kwargs):
            return func(self, *args, **kwargs) if not self.connection_is_lost else default
        return wrapper
    return decorator

def CallStoredProc(conn, procName, *args):
    sql = """SET NOCOUNT ON;
         DECLARE @ret int
         EXEC @ret = %s %s
         SELECT @ret""" % (procName, ','.join(['?'] * len(args)))
    return conn.execute(sql, args).fetchall()

def connect(func):
    """Open and close connection"""
    def wrapper(self, *args, **kwargs):
        conn, cursor = self.open()
        d = func(self, *args, conn=conn, cursor=cursor, **kwargs)
        self.close(conn, cursor)
        return d
    return wrapper

def choose_db(func):
    def wrapper(self, query, _save=True, **kwargs):
        """If connection is lost then adds query to json db.
        Args:
            query (_type_): query in str
            _save (bool, optional): Save to json if lost . Defaults to True.
        """        
        if self.connection_is_lost and _save: return self.save_to_json(query)
        else: func(self, query, **kwargs)
    return wrapper

def read_json(*names: str):
    with open(os.path.join(cur_dir, *names), 'r') as f:
        data = json.load(f)
    return data

def write_json(data:dict, *names: str):
    with open(os.path.join(cur_dir, *names), 'w') as f:
        json.dump(data, f)

def open_connection(host, user, password, database, DRIVER="{ODBC Driver 17 for SQL Server}"):
    return pyodbc.connect(f"DRIVER={DRIVER};SERVER={host};DATABASE={database};UID={user};PWD={password};TrustServerCertificate=YES")


class FailedConnection:
    def __init__(self, instance, host, user, password, database, driver):
        self.instance = instance
        self.host=host
        self.user=user
        self.password=password
        self.database=database
        self.driver = driver
        
    def reconnect(self):
        open_connection(self.host, self.user,
                        self.password, self.database, self.driver)
        return True


class Database:
    """Database instance"""    
    def __init__(
        self, 
        host: str, 
        user: str, 
        password: str, 
        database: str, 
        driver: str
    ) -> None:
        self.connection_is_lost = False
        self.host, self.user, self.password, self.database, self.driver = \
            host, user, password, database, driver
        self.threads = dict()
        
        #check for tmp data
        self.send_data_json_db()
        
    def open(self):
        if not self.connection_is_lost:
            try:
                conn = open_connection(self.host, self.user, self.password, self.database, self.driver)
                cursor = conn.cursor()
                self.connection_is_lost = False
                return conn, cursor
                
            except Exception as e:
                l.error(e)
                self.conn = FailedConnection(self, self.host, self.user, self.password, self.database, self.driver)
                self.connection_is_lost = True
                self.start_reconnecting()
        return (None, None)
                
    @default_if_lost(None)
    def close(self, conn, cursor):
        cursor.close()
        conn.close()

    @in_thread('r')
    def start_reconnecting(self) -> None:
        """Tries to reconnect to the database"""
        while self.threads['r']['running']:
            try:
                l.debug('is trying to reconnect')
                self.conn.reconnect()
                self.connection_is_lost = False
                self.send_data_json_db()
                break
            except pyodbc.Error:
                time.sleep(1)

    def send_data_json_db(self) -> None:
        """Rewrites data from json to db"""
        data = read_json("tmp_db_data.json")
        for query in data:
            self.query_string__insert(query)
        write_json([], "tmp_db_data.json")
        
    def save_to_json(
        self,
        query_string: str
    ) -> None:
        data = read_json("tmp_db_data.json")
        data.append(query_string)
        write_json(data, "tmp_db_data.json")
        
    def select(self, table: str, columns: list) -> list:
        query_string = f"SELECT {(','.join(columns))} FROM {table}"
        return self.query_string__select(query_string)
    
    def insert(self, table: str, columns: list, data: list, _save=True) -> None:
        query_string = f"INSERT INTO {table}({(','.join(columns))})\
            VALUES ({','.join([repr_single(x) for x in data])})"
        self.query_string__insert(query_string, _save)
            
    @connect
    @default_if_lost([])
    def call_proc(self, proc, args=[], q=True, **k) -> List:
        """q: query or not"""
        sql = f"exec {proc} {' '.join(args) if len(args) != 0 else ''}"
        conn, cursor = k['conn'], k['cursor']
        cursor.execute(sql)
        results = cursor.fetchall() if q else None
        conn.commit()
        return results
    
    @connect
    @choose_db
    def query_string__insert(self, query_string: str, _save=True, q=True, **k) -> None | list:
        return self._execute(k['conn'], k['cursor'], query_string, q=False)
    
    @connect
    @default_if_lost([])
    def query_string__select(self, query_string: str, q=True, **k) -> None | list:
        return self._execute(k['conn'], k['cursor'], query_string, q)
    
    def _execute(self, conn, cursor, query_string: str, q:bool=True) -> None | list:
        """Execute query
        Args:
            q (bool): if true then is query otherwise false(no fetch)
        Returns:
            None | list: data or nothing(in case of insert)
        """
        cursor.execute(query_string)
        response = cursor.fetchall() if q else None
        conn.commit()
        return response

    def get_where(self, table: str, columns: list, values: list) -> None | list:
        values = [repr_single(x) for x in values]
        _where = " AND ".join(
            f"{name} = {value}" for name, value in zip(columns, values))
        query_string = f'SELECT * FROM {table} WHERE {_where};'
        return self.query_string__select(query_string)
    
    def stop_threads(self):
        for thread in self.threads.values():
            thread['running'] = False

    def _except_t_job_error(self):
        try:
            self.call_proc("msdb.dbo.sp_start_job", ["'MS_SQL2MS_SQL'"], q = False)
        except pyodbc.Error as ex:
            if ex.args[0] != '42000':
                raise pyodbc.Error(ex)

    def execute_with_check(self, func):
        """Check whether mssql to sql works and execute. Decorator """
        def wrapper(*args, **kwargs):
            v = func(*args, **kwargs)
            self._except_t_job_error()
            return v
        return wrapper
