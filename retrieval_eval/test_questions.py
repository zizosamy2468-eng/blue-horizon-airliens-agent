# retrieval_eval/test_questions.py
#
# RETRIEVAL EVAL TEST SET.
#
# 12 questions across three categories, matching the lab's requirement of
# at least one question per category that should specifically favor each
# architecture:
#
#   - "general": broad conceptual questions naive RAG should handle fine --
#     the question's meaning embeds well, no exact identifier needed.
#   - "citation": questions that name an exact policy code or a very
#     specific term. Naive vector search struggles here because "4.2b"
#     doesn't embed distinctively; hybrid search (BM25 exact-match
#     component) should win.
#   - "multi_part": questions that genuinely need two or more DIFFERENT
#     policy sections combined (e.g. a compensation rule AND a duty-time
#     rule together) -- naive/hybrid single-shot retrieval on the whole
#     question tends to only surface one side of it well; agentic RAG's
#     multi-hop loop should win by searching each part separately.
#
# expected_codes is the ground truth used by run_eval.py: an architecture's
# retrieval for a given question is scored correct only if ALL expected
# codes were retrieved (not just one), since a genuinely correct answer to
# a multi-part question needs every relevant section, not just any one of
# them.

from dataclasses import dataclass


@dataclass
class TestQuestion:
    question: str
    category: str  # "general" | "citation" | "multi_part"
    expected_codes: list[str]


TEST_QUESTIONS: list[TestQuestion] = [
    # ---------------- general (naive-friendly) ----------------
    TestQuestion(
        question="What happens with compensation for a passenger whose flight was disrupted by weather?",
        category="general",
        expected_codes=["IROPS-COMP-2"],
    ),
    TestQuestion(
        question="What are the standard maximum flying and duty hours for a crew member in one day?",
        category="general",
        expected_codes=["IROPS-DUTY-1"],
    ),
    TestQuestion(
        question="What information does a passenger disruption notice need to include?",
        category="general",
        expected_codes=["IROPS-COMM-1"],
    ),
    TestQuestion(
        question="What order are passengers prioritized in when rebooking onto a limited-availability replacement flight?",
        category="general",
        expected_codes=["IROPS-REBOOK-1"],
    ),
    # ---------------- citation-heavy (hybrid-favoring) ----------------
    TestQuestion(
        question="What does policy 4.2b say about the compensation auto-approve threshold?",
        category="citation",
        expected_codes=["IROPS-COMP-4.2b"],
    ),
    TestQuestion(
        question="Can a duty-time override granted under IROPS-DUTY-3 be reused for a second flight the same day?",
        category="citation",
        expected_codes=["IROPS-DUTY-5"],
    ),
    TestQuestion(
        question="Summarize what's in the compensation policy update log entry IROPS-COMP-6.",
        category="citation",
        expected_codes=["IROPS-COMP-6"],
    ),
    TestQuestion(
        question="What does IROPS-CREW-2 say about preferring a crew member's base airport?",
        category="citation",
        expected_codes=["IROPS-CREW-2"],
    ),
    # ---------------- multi-part (agentic-favoring) ----------------
    TestQuestion(
        question=(
            "A gold-tier passenger is affected by a mechanical disruption, and the reserve "
            "crew member we'd assign already needs a duty-time override. What compensation "
            "applies to the passenger, and what has to be true before we can assign that crew member?"
        ),
        category="multi_part",
        expected_codes=["IROPS-COMP-5", "IROPS-DUTY-3"],
    ),
    TestQuestion(
        question=(
            "If maintenance later reclassifies a flight's disruption cause from mechanical to "
            "weather, what should happen to compensation already issued, and how should the new "
            "cause be logged?"
        ),
        category="multi_part",
        expected_codes=["IROPS-MECH-1", "IROPS-WEATHER-1"],
    ),
    TestQuestion(
        question=(
            "Before requesting a supervisor override for a crew member's duty hours, what should "
            "be checked first, and once granted, how far does that override extend?"
        ),
        category="multi_part",
        expected_codes=["IROPS-DUTY-4", "IROPS-DUTY-5"],
    ),
    TestQuestion(
        question=(
            "For a disrupted flight affecting more than 20 bookings where some passengers also "
            "need rebooking onto a limited-availability flight, what escalation is required and "
            "what determines rebooking order?"
        ),
        category="multi_part",
        expected_codes=["IROPS-COMM-2", "IROPS-REBOOK-1"],
    ),
]


if __name__ == "__main__":
    by_category = {}
    for q in TEST_QUESTIONS:
        by_category.setdefault(q.category, []).append(q)

    for category, questions in by_category.items():
        print(f"=== {category} ({len(questions)} questions) ===")
        for q in questions:
            print(f"  - {q.question[:80]}... -> {q.expected_codes}")
        print()