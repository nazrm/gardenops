# Recording And Using Garden History

GardenOps remains map-first. Search finds plants and places. A plant name opens
a read-only summary with its current locations, observations and history;
Edit remains a separate action. Containers open the same details from a marker
or a location link. An area's Details action opens its existing controls.
Main tabs remember their last subview for this session, until a garden or
account change.

## Record In Context

Use Record observation or Report issue from a plant or place. The form carries
that context, but linked plants and places can be changed before saving. A plant
record alone does not claim that every one of its locations was inspected.
Issues can be entered manually; photo diagnosis is optional.

New journal text drafts survive closing the form or reloading in the same
browser. Resume or discard the existing draft before starting another. Photos
must be reselected for an unsent draft. Submitted new entries, including their
photos, go through the durable offline queue. If attachments fail after the
entry saves, retry uploads, open the saved entry, or discard the remaining
attachments. Retrying does not create another parent entry. Sign-out clears
private local drafts through the normal offline-work cleanup.

Older queued entries without verifiable ownership are not replayed or shown to
another account. The offline indicator shows only their count and offers an
explicit confirmed discard. Sync older work before upgrading where possible;
the app cannot safely infer its owner. Authentication network failures preserve
local work behind a retry screen rather than treating the failure as sign-out.
Editing a saved entry also retains photo upload/link progress on ordinary retry.

History is available even when no observations exist. Its plant/place scope
survives filters and pagination; Clear scope removes it and Return restores
the originating view. Historical locations remain those recorded at the time.

## Complete Actual Work

Bloom, pruning and fertilizing completion asks for the date the work or
observation occurred. Today follows the configured garden observation timezone.
Future dates are rejected. Select only the plants actually treated in grouped
work; the others remain pending. Grouped pruning/fertilizing also supports
offline submission with the same selection and stale-task checks.

For bloom, select where it was observed from current plant assignments, not the
task's older location links. Grouped selections offer only places shared by all
selected plants; select a smaller group to record different locations. Offline,
only assignments confirmed earlier in this session are available. No selected location records a
plant-level observation without changing a placement's observation state.
Weekly snooze is just deferral, not evidence that a plant was inspected.
Closing the bloom season is a separate confirmed action for the displayed year.
Correct that observation through its history; snoozing does not undo it.
"Not seen" does not mean dead or removed. Generated bloom task details explain
whether their timing uses local observations or catalogue months.

## Plan With Recorded Evidence

Inspect a planner candidate to see linked stock in its original units and
plant it using the existing quantity-confirmation form. Its quantity consumes
stock units and is recorded in history; it does not convert packets or pieces
into a specimen count. The existing placement command adds the plant to the
destination without increasing an existing placement count. Recorded here shows
bounded journal and harvest history for that plant and historical place. No
history means no record, not that the plant failed or succeeded.

Harvest list and summary use the same year or custom date bounds and quality
filter. Quantities remain separate by unit. Shared-entry quantities are marked
as shared; plant-attributed rows must not be added together as garden yield.
Matrix save receipts name the confirmed plants, places and recorded dates or
quantities, with a reference for retry tracking.
An ambiguous bloom message asks for clarification. A request to defer does not
record bloom or change the due date; its response directs you to snooze in Tasks.
