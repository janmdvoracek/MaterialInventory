# StockMovement is deliberately NOT registered here.
#
# A line item never exists apart from the job it belongs to, and WorkOrderAdmin
# already edits them as an inline — registering it as well put a second
# top-level section in the admin index ("Zpracování", the app's own label) that
# held one model reachable from the job anyway, and that read like the worker
# form of the same name. Everything about a job is now edited in one place,
# under Zakázky.
