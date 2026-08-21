from spike.store import TicketStore


store = TicketStore()
try:
    store.add("   ")
except ValueError:
    pass
else:
    raise SystemExit(1)

first = store.add("Fire the risotto")
second = store.add("Wipe the pass")
store.delete(first.id)
raise SystemExit(0 if [ticket.id for ticket in store.all()] == [second.id] else 1)
