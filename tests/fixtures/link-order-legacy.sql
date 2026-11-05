BEGIN TRANSACTION;
CREATE TABLE choice_events (
            round_id      TEXT PRIMARY KEY NOT NULL,
            payload_hash  TEXT NOT NULL,
            result_json   TEXT NOT NULL,
            created       TEXT NOT NULL
        );
INSERT INTO "choice_events" VALUES('8acbbc70-663f-44f8-8ffb-e24c17db0808','ae25f4208957d25077108159d847c0912940ebc0f2065b3edfe9a9352927f972','{"chosen": "forward", "label": "Keep original", "feedback_applied": true, "learning_context": {"section": "methods", "role": "detail"}, "features": 33, "syntax_features": 25, "syntax_backend": "heuristic", "heuristic_template_active": false, "syntax_requested_backend": "heuristic", "syntax_fallback_reason": null, "syntax_fallbacks": {}, "base_syntax_negative_feedback": true, "syntax_model_version": null, "syntax_model_fingerprint": null, "syntax_model_fingerprints": {"forward": null, "reverse": null}, "author_exemplar": true, "round_id": "8acbbc70-663f-44f8-8ffb-e24c17db0808", "idempotent": true, "replayed": false, "analysis_features": 4, "analysis_input_version": 2, "analysis_feature_capacity": 4, "analysis_topology_features": 2, "analysis_topology_capacity": 2, "analysis_topology_feature_version": "v1", "analysis_source": "writing-model", "analysis_feature_version": "v2", "analysis_status": "complete", "analysis_negative_feedback": true, "analysis_topology_negative_feedback": true, "grammar_features": 19, "grammar_source": "writing-model", "grammar_input_version": 6, "grammar_feature_version": "v1", "grammar_status": "partial", "grammar_negative_feedback": false, "argument_shape_negative_feedback": false, "adjunct_position_negative_feedback": false, "edit_scope": {"before_text": "温度稳定后，系统开始采样。如果噪声超标，系统重复测量。", "keep_original_id": "forward", "sentence_ids": ["S001", "S002"]}}','2026-10-27T12:00:00+00:00');
CREATE TABLE exemplars (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            kind     TEXT NOT NULL,
            text     TEXT NOT NULL,
            ngram    TEXT NOT NULL,
            created  TEXT NOT NULL,
            last_at  TEXT NOT NULL,
            rewards  INTEGER NOT NULL DEFAULT 0
        );
INSERT INTO "exemplars" VALUES(1,'author','温度稳定后，系统开始采样。如果噪声超标，系统重复测量。','{"  温": 1, " 后 ": 1, " 噪 ": 1, " 声 ": 1, " 复 ": 1, " 如 ": 1, " 始 ": 1, " 定 ": 1, " 度 ": 1, " 开 ": 1, " 果 ": 1, " 标 ": 1, " 样 ": 1, " 测 ": 1, " 温 ": 1, " 稳 ": 1, " 系 ": 2, " 统 ": 2, " 超 ": 1, " 采 ": 1, " 重 ": 1, " 量 ": 1, "后 系": 1, "噪 声": 1, "声 超": 1, "复 测": 1, "如 果": 1, "始 采": 1, "定 后": 1, "度 稳": 1, "开 始": 1, "果 噪": 1, "标 系": 1, "样 如": 1, "测 量": 1, "温 度": 1, "稳 定": 1, "系 统": 2, "统 开": 1, "统 重": 1, "超 标": 1, "采 样": 1, "重 复": 1, "量  ": 1}','2026-10-27T12:00:00+00:00','2026-10-27T12:00:00+00:00',1);
CREATE TABLE phrase_prefs (
            context  TEXT NOT NULL,
            feature  TEXT NOT NULL,
            weight   INTEGER NOT NULL DEFAULT 0,
            picks    INTEGER NOT NULL DEFAULT 0,
            last_at  TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (context, feature)
        );
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk2:噪声',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk2:声超',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk2:复测',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk2:如果',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk2:始采',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk2:定后',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk2:度稳',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk2:开始',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk2:果噪',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk2:测量',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk2:温度',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk2:稳定',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk2:系统',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk2:统开',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk2:统重',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk2:超标',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk2:采样',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk2:重复',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk3:噪声超',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk3:声超标',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk3:复测量',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk3:如果噪',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk3:始采样',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk3:度稳定',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk3:开始采',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk3:果噪声',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk3:温度稳',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk3:稳定后',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk3:系统开',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk3:系统重',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk3:统开始',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk3:统重复',1000,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "phrase_prefs" VALUES('methods::detail','cjk3:重复测',1000,1,'2026-10-27T12:00:00+00:00');
CREATE TABLE prefs (
            context  TEXT NOT NULL,
            opt      TEXT NOT NULL,
            weight   INTEGER NOT NULL DEFAULT 0,
            picks    INTEGER NOT NULL DEFAULT 0,
            last_at  TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (context, opt)
        );
INSERT INTO "prefs" VALUES('methods::detail','Keep original',1000,1,'2026-10-27T12:00:00+00:00');
CREATE TABLE seen (
            context TEXT NOT NULL,
            opt     TEXT NOT NULL,
            at      TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (context, opt)
        );
CREATE TABLE syntax_prefs (
            context  TEXT NOT NULL,
            feature  TEXT NOT NULL,
            weight   INTEGER NOT NULL DEFAULT 0,
            picks    INTEGER NOT NULL DEFAULT 0,
            last_at  TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (context, feature)
        );
INSERT INTO "syntax_prefs" VALUES('methods::detail','syntax:v3:heuristic:heuristic_clauses:short,short,short,short',1500,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "syntax_prefs" VALUES('methods::detail','syntax:v3:heuristic:heuristic_punct:，。，。',1500,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "syntax_prefs" VALUES('methods::detail','syntax:analysis-topology-v1:writing-model:pair:method>method:AB',1500,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "syntax_prefs" VALUES('methods::detail','syntax:analysis-topology-v1:writing-model:pair_signature:method/affirmative/asserted>method/affirmative/asserted:AB',1500,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "syntax_prefs" VALUES('methods::detail','syntax:analysis-v2:writing-model:move_pos:method@only',1500,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "syntax_prefs" VALUES('methods::detail','syntax:analysis-v2:writing-model:signature_pos:method/affirmative/asserted@only',1500,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "syntax_prefs" VALUES('methods::detail','syntax:grammar-v1:writing-model:adjunct_position:temporal:before',1500,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "syntax_prefs" VALUES('methods::detail','syntax:grammar-v1:writing-model:expression:adjectival/nonagentive/patient>event',1500,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "syntax_prefs" VALUES('methods::detail','syntax:grammar-v1:writing-model:expression:verbal/active/agent>event>complement',1500,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "syntax_prefs" VALUES('methods::detail','syntax:grammar-v1:writing-model:expression:verbal/active/event',1500,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "syntax_prefs" VALUES('methods::detail','syntax:grammar-v1:writing-model:expression:verbal/nonagentive/patient>event',1500,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "syntax_prefs" VALUES('methods::detail','syntax:grammar-v1:writing-model:expression_slots:adjectival:patient>event',1500,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "syntax_prefs" VALUES('methods::detail','syntax:grammar-v1:writing-model:expression_slots:verbal:agent>event>complement',1500,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "syntax_prefs" VALUES('methods::detail','syntax:grammar-v1:writing-model:expression_slots:verbal:event',1500,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "syntax_prefs" VALUES('methods::detail','syntax:grammar-v1:writing-model:expression_slots:verbal:patient>event',1500,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "syntax_prefs" VALUES('methods::detail','syntax:grammar-v1:writing-model:form:adjectival',1500,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "syntax_prefs" VALUES('methods::detail','syntax:grammar-v1:writing-model:form:verbal',1500,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "syntax_prefs" VALUES('methods::detail','syntax:grammar-v1:writing-model:link:complement',1500,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "syntax_prefs" VALUES('methods::detail','syntax:grammar-v1:writing-model:link:condition',1500,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "syntax_prefs" VALUES('methods::detail','syntax:grammar-v1:writing-model:link:temporal',1500,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "syntax_prefs" VALUES('methods::detail','syntax:grammar-v1:writing-model:link_order:complement:head>dependent',1500,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "syntax_prefs" VALUES('methods::detail','syntax:grammar-v1:writing-model:link_order:condition:dependent>head',1500,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "syntax_prefs" VALUES('methods::detail','syntax:grammar-v1:writing-model:link_order:temporal:dependent>head',1500,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "syntax_prefs" VALUES('methods::detail','syntax:grammar-v1:writing-model:voice:active',1500,1,'2026-10-27T12:00:00+00:00');
INSERT INTO "syntax_prefs" VALUES('methods::detail','syntax:grammar-v1:writing-model:voice:nonagentive',1500,1,'2026-10-27T12:00:00+00:00');
CREATE UNIQUE INDEX exemplars_kind_text ON exemplars(kind,text);
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('exemplars',1);
COMMIT;
