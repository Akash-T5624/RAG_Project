"""Week-8 trajectory eval for the generic RAG Q&A agent (backend spec).

Scores the agent's *path* (the ordered tool-call sequence), not just its
final answer.  A case passes the outcome eval when its answer carries all
expected keywords; it passes the trajectory eval when its tool sequence
matches one of the accepted "good" paths for that question.  The gap between
the two is the false-positive rate — right answer, wrong process.
"""