import backend.db as db
def guess_lang(pin):  # English only
    return "en-IN"

ROWS = [
 ("Ravi Kumar","+919000000001","Flat 302, Sri Sai Residency, Benz Circle, Vijayawada","Near Benz Circle metro pillar","520010","Bluetooth Speaker",1499),
 ("Lakshmi Devi","+919000000002","12-4-7, Gandhi Nagar, Vijayawada","Opp. Ram Mandir","520003","Mixer Grinder",3299),
 ("Amit Sharma","+919000000003","B-45, Sector 15, Rohini, Delhi","Behind Metro Hospital","110089","Running Shoes",2199),
 ("Priya Singh","+919000000004","H.No 8, Lajpat Nagar II, Delhi","Near sweet shop","110024","Backpack",999),
 ("Suresh Reddy","+919000000005","Plot 21, Madhapur, Hyderabad","Next to Cafe Coffee Day","500081","Smart Watch",4999),
 ("John Mathew","+919000000006","Apt 5C, Brigade Road, Bengaluru","Above Reliance Trends","560001","Headphones",2599),
 ("Sunita Rao","+919000000007","3rd Floor, Anna Nagar, Chennai","Near Murugan temple","600040","Air Fryer",5499),
 ("Karan Mehta","+919000000008","C-12, Vasant Vihar, Delhi","Gate 2, near park","110057","Kurta Set",1299),
]
def run(force=False):
    if force:
        db.q("delete from orders"); db.q("delete from agents")
    if db.q("select count(*) c from agents")[0]["c"]: return
    db.q("insert into agents values(1,'Ramesh (Vijayawada/Hyd)')"); db.q("insert into agents values(2,'Imran (Delhi/South)')")
    for i, r in enumerate(ROWS):
        aid = 1 if i < 5 else 2
        db.q("insert into orders(agent_id,seq,customer,phone,address,landmark,pincode,product,amount,lang) values(?,?,?,?,?,?,?,?,?,?)",
             (aid, i+1, *r[:5], r[5], r[6], guess_lang(r[4])))
