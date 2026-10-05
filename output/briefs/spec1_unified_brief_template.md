# SPEC-1 DAILY BRIEF — {{DATE_YYYY-MM-DD}}

## Executive Summary
- {{EXEC_SUMMARY_POINT_1}}
- {{EXEC_SUMMARY_POINT_2}}
- {{EXEC_SUMMARY_POINT_3}}
- {{OPTIONAL_EXEC_SUMMARY_POINT_4}}
- {{OPTIONAL_EXEC_SUMMARY_POINT_5}}

## Elevated Signals
{{#IF_ELEVATED_SIGNALS}}
1. **Signal:** {{ELEVATED_SIGNAL_1_NAME}}  
   **Type:** {{GEOPOLITICAL_OR_CYBER}}  
   **Confidence:** {{LOW|MODERATE|HIGH}}  
   **Assessment:** {{ELEVATED_SIGNAL_1_ASSESSMENT}}  
   **Actionability:** {{IMMEDIATE_ACTION_RECOMMENDED_OR_NOT}}

{{/IF_ELEVATED_SIGNALS}}
{{#IF_NO_ELEVATED_SIGNALS}}
- None identified.
- No signal met threshold for elevation into priority geopolitical or cyber analysis.
{{/IF_NO_ELEVATED_SIGNALS}}

## Below-Threshold Signals
1. **Signal Count:** {{TOTAL_SIGNAL_COUNT}} total harvested inputs.  
   **Disposition:** Below threshold.  
   **Assessment:** {{BT_SIGNAL_1_ASSESSMENT}}

2. **Threshold Result:** {{THRESHOLD_RESULT_SUMMARY}}  
   **Disposition:** {{NON_ESCALATORY_OR_OTHER}}  
   **Assessment:** {{BT_SIGNAL_2_ASSESSMENT}}

3. **Operational Relevance:** {{LOW|MODERATE|HIGH}} current actionability.  
   **Disposition:** {{LOGGED|DISMISSED|WATCHLIST}}  
   **Assessment:** {{BT_SIGNAL_3_ASSESSMENT}}

## Confidence & Gaps
- **Overall confidence:** {{LOW|LOW_TO_MODERATE|MODERATE|HIGH}}
- **Primary limitation:** {{PRIMARY_LIMITATION}}
- **Collection gap(s):** {{COLLECTION_GAPS}}
- **Analytical constraint(s):** {{ANALYTICAL_CONSTRAINTS}}
- **Uncertainty statement:** {{WHAT_IS_UNKNOWN_AND_WHY}}

## Analyst Notes
- **Assumptions**
  - {{ASSUMPTION_1}}
  - {{ASSUMPTION_2}}

- **Data quality issues**
  - {{DATA_QUALITY_ISSUE_1}}
  - {{DATA_QUALITY_ISSUE_2}}

- **Recommended next collection steps**
  1. {{NEXT_STEP_1}}
  2. {{NEXT_STEP_2}}
  3. {{NEXT_STEP_3}}
  4. {{NEXT_STEP_4}}

---

## Consolidated Source Context (Verbatim Metadata)
- **Repository:** `{{OWNER}}/{{REPO}}`
- **Repository ID:** `{{REPO_ID}}`
- **Description:** {{REPO_DESCRIPTION}}
- **Language composition:**
  - {{LANG_1_NAME}}: {{LANG_1_PERCENT}}%
  - {{LANG_2_NAME}}: {{LANG_2_PERCENT}}%
  - {{OPTIONAL_LANG_3_NAME}}: {{OPTIONAL_LANG_3_PERCENT}}%

## Cycle Inputs (Chronological)
1. **Cycle:** {{CYCLE_ID_OR_TIMESTAMP_1}}  
   **Raw Input:** {{RAW_INPUT_1}}  
   **Score/Confidence:** {{SCORE_1}}  
   **Threshold Status:** {{BELOW|AT|ABOVE}}  
   **Notes:** {{NOTES_1}}

2. **Cycle:** {{CYCLE_ID_OR_TIMESTAMP_2}}  
   **Raw Input:** {{RAW_INPUT_2}}  
   **Score/Confidence:** {{SCORE_2}}  
   **Threshold Status:** {{BELOW|AT|ABOVE}}  
   **Notes:** {{NOTES_2}}

3. **Cycle:** {{CYCLE_ID_OR_TIMESTAMP_3}}  
   **Raw Input:** {{RAW_INPUT_3}}  
   **Score/Confidence:** {{SCORE_3}}  
   **Threshold Status:** {{BELOW|AT|ABOVE}}  
   **Notes:** {{NOTES_3}}

## Prompt-Ready Block (Optional)
Use this section for direct paste into prompted AI systems:

You are an intelligence-grade editorial analyst. Produce a concise, evidence-first daily brief in clear U.S. English, with neutral tone and publication-quality structure. Use only provided source material. Do not invent facts. Separate observed facts from assessment. Highlight uncertainty and confidence. If no elevated signals are present, state that clearly.

Required output sections:
1) Executive Summary  
2) Elevated Signals  
3) Below-Threshold Signals  
4) Confidence & Gaps  
5) Analyst Notes
