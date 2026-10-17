# MaterialInventory

Records material processing jobs and machine refuellings for a depot, and reports on them. It does not track stock.

## Language

**Entry**:
One recorded piece of work that goes through review: a job or a refuelling.
_Avoid_: record, submission (a submission is the act of saving an entry)

**Entry page**:
The page on which an entry is recorded or corrected: its own fields, then its sections.
_Avoid_: entry form (the form is only part of the page)

**Section**:
A titled group of rows on an entry page, such as Spotřebováno, Vyrobeno, Spolupracovníci, Použité stroje or Tankované stroje; each names a given material, machine or person at most once.
_Avoid_: formset, table

**Row**:
One line of a section: one material, machine or person with its numbers, either filled in or left blank.
_Avoid_: item, line item (a line item is a stored consumed or produced row)
