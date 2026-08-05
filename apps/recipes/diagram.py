"""Laying a recipe out as a Cooking-for-Engineers table.

The diagram everybody recognises — ingredients down the left, operations
merging leftwards to rightwards until one cell says "bake" — is a **tree drawn
with rowspans**. Getting it out of the database and onto a page is one
algorithm with three parts, and each of them is a decision worth stating,
because the obvious version of each is wrong in a way that still renders.

**The column an operation sits in is not its depth.** It is one more than the
deepest operation feeding it::

    column(step) = 1 + max(column(child steps), default 0)

Depth-from-the-root looks equivalent on a straight chain and falls apart the
moment a recipe branches: two operations that should be side by side in the
same column (a sauce simmering while a filling is mixed) land in different ones
and the table grows a ragged staircase. Counting from the leaves puts every
operation as far left as it can go, which is what makes the merge read.

**A cell has to reach its parent.** An operation at column 2 whose parent is at
column 5 spans the three columns between them (``colspan = parent - own``),
because the parent's column is fixed by its *deepest* child and every shallower
one has to stretch to meet it. Without this the branch that merges late leaves
a hole and every cell to its right shifts up a column.

**Empty space is a cell too.** An ingredient that goes straight into the
operation at column 4 has nothing in columns 1 to 3, and a table row that
simply omits those cells is a row that has silently moved everything after it
one column left. The gap is rendered as a `filler` cell with no borders, which
is the same picture and a valid table.

Everything here is pure: it reads model instances and returns dataclasses, so
the caller can hand it prefetched lists and the page stays at a flat query
count however many steps a recipe has.
"""

from dataclasses import dataclass, field


@dataclass
class Cell:
    """One ``<td>``. ``kind`` is what the template switches on."""

    kind: str                      # "ingredient" | "step" | "filler"
    rowspan: int = 1
    colspan: int = 1
    ingredient: object = None
    step: object = None
    # The substitutes for this ingredient — rendered under it in the same cell,
    # because a substitute is not a row of its own in the diagram: it does not
    # take part in the merge, it replaces something that does.
    alternatives: list = field(default_factory=list)


@dataclass
class CookStep:
    """One stop on the guided walk through a recipe.

    Carries what the cooking view needs on screen at that moment and nothing
    else: the operation, the lines that go into it, and the operations whose
    output it consumes (which is how "add the sauce from step 2" gets said).
    """

    step: object
    number: int
    ingredients: list = field(default_factory=list)
    from_steps: list = field(default_factory=list)


@dataclass
class Diagram:
    """The whole table. ``blocks`` are ``<tbody>`` groups — one per root."""

    columns: int = 1
    blocks: list = field(default_factory=list)
    order: list = field(default_factory=list)     # CookStep, in the order to cook
    unplaced: list = field(default_factory=list)  # ingredients in no step at all

    def __bool__(self):
        """A recipe with no steps has no diagram — not an empty one.

        Templates ask ``{% if diagram %}``, and every recipe written before
        this feature existed has ingredients and no steps. Those pages must
        show the ingredient list they always showed rather than a one-column
        table pretending to be a diagram.
        """
        return bool(self.order)


def top_level(ingredients):
    """The real ingredient lines, each carrying its substitutes as ``.substitutes``.

    Grouped here, in Python, over a list the caller already has — rather than
    with a ``prefetch_related("alternatives")``, which is a second query per
    page for rows that are in the first one. Both the diagram and the plain
    ingredient list read the result, so neither can disagree with the other
    about what counts as a line.
    """
    alternatives, lines = {}, []
    for item in ingredients:
        if item.alternative_for_id:
            alternatives.setdefault(item.alternative_for_id, []).append(item)
        else:
            lines.append(item)
    for item in lines:
        item.substitutes = alternatives.get(item.id, [])
    return lines


def build(recipe, ingredients=None, steps=None):
    """Lay ``recipe`` out. Pass prefetched lists to keep the page's query count flat."""
    steps = list(recipe.steps.all()) if steps is None else list(steps)
    rows = list(recipe.ingredients.all()) if ingredients is None else list(ingredients)

    leaves = top_level(rows)

    by_id = {step.id: step for step in steps}
    children = {step.id: [] for step in steps}
    roots = []
    for step in steps:
        parent_id = step.parent_id if _terminates(step, by_id, len(steps)) else None
        if parent_id and parent_id in by_id and parent_id != step.id:
            children[parent_id].append(step)
        else:
            roots.append(step)

    inputs = {}
    unplaced = []
    for item in leaves:
        if item.step_id in children:
            inputs.setdefault(item.step_id, []).append(item)
        else:
            unplaced.append(item)

    if not steps:
        return Diagram(columns=1, unplaced=unplaced)

    column = {}
    for step in steps:
        _column(step, children, column)
    total = 1 + max(column.values(), default=0)

    def block(step, parent_column):
        own = column[step.id]
        laid = []
        # Child operations first, then the loose ingredients — which is the
        # reading order of every diagram of this kind: the pot you already have
        # at the top of the group, the things being added to it underneath.
        for child in children[step.id]:
            laid.extend(block(child, own))
        for item in inputs.get(step.id, []):
            row = [Cell("ingredient", ingredient=item, alternatives=item.substitutes)]
            if own > 1:
                row.append(Cell("filler", colspan=own - 1))
            laid.append(row)
        if not laid:
            # An operation with no inputs that nevertheless feeds another one —
            # "bring a pan of water to the boil" before "cook the pasta in it".
            # It still needs the columns to its left filled or the row shifts.
            laid = [[Cell("filler", colspan=own)]]
        laid[0].append(Cell("step", step=step, rowspan=len(laid),
                            colspan=max(1, parent_column - own)))
        return laid

    blocks = []
    for root in roots:
        if not children[root.id] and not inputs.get(root.id):
            # A standing instruction with nothing flowing into it: the two rows
            # across the top of the reference diagram, "Butter and flour an
            # 8x8-in pan" and "Preheat oven to 350°F". Full width.
            blocks.append([[Cell("step", step=root, colspan=total)]])
        else:
            blocks.append(block(root, total))

    if unplaced:
        # Ingredients nobody has assigned to an operation yet. Last, so the
        # diagram proper keeps its shape, and visible, so they are not lost.
        trailing = []
        for item in unplaced:
            row = [Cell("ingredient", ingredient=item, alternatives=item.substitutes)]
            if total > 1:
                row.append(Cell("filler", colspan=total - 1))
            trailing.append(row)
        blocks.append(trailing)

    return Diagram(columns=total, blocks=blocks,
                   order=_cook_order(roots, children, inputs), unplaced=unplaced)


def _column(step, children, memo):
    """One more than the deepest operation feeding this one. Memoised.

    Iterative rather than recursive: the recursion is bounded by how deeply a
    person nested their recipe, which is small, but a stack overflow on a data
    shape is a 500 nobody can read.
    """
    stack = [step]
    while stack:
        current = stack[-1]
        pending = [c for c in children[current.id] if c.id not in memo]
        if pending:
            stack.extend(pending)
            continue
        memo[current.id] = 1 + max((memo[c.id] for c in children[current.id]), default=0)
        stack.pop()
    return memo[step.id]


def _terminates(step, by_id, limit):
    """Whether following ``parent`` from here reaches a root.

    A cycle cannot be created through the form — the wiring in forms.py refuses
    one — but it can be created by hand in the Django admin or by an edit
    straight to the database, and a cycle here is an infinite loop inside a
    page render rather than a wrong picture. A step whose chain does not
    terminate is treated as a root, which draws something slightly odd and
    keeps the page alive.
    """
    current, hops = step, 0
    while current is not None and current.parent_id:
        current = by_id.get(current.parent_id)
        hops += 1
        if hops > limit:
            return False
    return True


def _cook_order(roots, children, inputs):
    """The steps in the order somebody would do them: children before parents.

    Post-order, roots in their own order. That puts "preheat the oven" (a root
    with nothing feeding it) wherever it was placed on the form, and every
    merge after both of the things it merges.
    """
    order = []

    def walk(step):
        for child in children[step.id]:
            walk(child)
        order.append(CookStep(
            step=step,
            number=len(order) + 1,
            ingredients=list(inputs.get(step.id, [])),
            from_steps=list(children[step.id]),
        ))

    for root in roots:
        walk(root)
    return order
