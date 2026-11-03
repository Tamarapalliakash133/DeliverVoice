import sqlite3, threading, time, os
C = sqlite3.connect(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "delivercall.db"), check_same_thread=False)
C.row_factory = sqlite3.Row
_l = threading.Lock()

def q(sql, args=()):
    with _l:
        cur = C.execute(sql, args)
        rows = [dict(r) for r in cur.fetchall()]
        C.commit()
        return rows

def get(oid):
    r = q("select * from orders where id=?", (oid,))
    return r[0] if r else None

def update(oid, **kw):
    kw["updated"] = time.time()
    q(f"update orders set {','.join(k+'=?' for k in kw)} where id=?", (*kw.values(), oid))

def init():
    q("create table if not exists agents(id integer primary key, name text)")
    q("""create table if not exists orders(id integer primary key, agent_id int, seq int, customer text,
        phone text, address text, landmark text, pincode text, product text, amount int, lang text,
        status text default 'pending', slot text, reason text, addr_note text, attempts int default 0,
        next_retry real, sms_sent int default 0, transcript text default '[]', updated real)""")
