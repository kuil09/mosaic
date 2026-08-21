from spike.store import TicketStore


store = TicketStore()
store.add("Fire the risotto")
completed = store.add("Wipe the pass")
store.complete(completed.id)
active_ids = [ticket.id for ticket in store.active()]
raise SystemExit(0 if completed.done and completed.id not in active_ids else 1)
