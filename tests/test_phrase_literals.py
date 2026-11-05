"""Scientific literals are preserved as text, not learned as lexical taste."""
from contextlib import closing
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('phrase_literals', ROOT / 'library/learn.py')
learn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(learn)


class PhraseLiteralTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='sonder-literals-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = str(self.root / 'preference.db')
        self.file = self.root / 'choice.json'
        clock = patch.object(learn, '_now_iso', return_value='2026-10-27T02:00:00+00:00')
        clock.start(); self.addCleanup(clock.stop)

    def choose(self, text, round_id='round'):
        data = {'round_id': round_id, 'learning_context': {'section':'results','role':'support'},
                'candidates':[{'id':'own','label':'Neutral','text':text}], 'chosen':'own','author_owned':True}
        self.file.write_text(json.dumps(data, ensure_ascii=False))
        return learn.choice_file(self.db,'results','support',self.file)

    def rows(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return list(conn.iterdump())

    def test_numbers_and_exponents_are_not_phrase_features_but_named_terms_are(self):
        features = learn._phrase_features('We compare ResNet50 H2O p53 in 12 samples at 0.42 and 1e12, -2.5E-08, .7e+12.')
        self.assertEqual(features, sorted('word:'+w for w in ('we','compare','resnet50','h2o','p53','in','samples','at','and')))
        self.assertIn('phrase_literal_guard', learn.engine_info()['capabilities'])

    def test_complete_math_regions_never_contribute_latin_or_chinese_tokens(self):
        regions = [r'$\alpha_{2024}=12\text{不可识别词}$', r'$$\frac{noise}{power}=12$$',
                   r'\(\theta_{2025}=13\)', r'\[\text{纯粹数据}\]',
                   r'\begin{equation*}\begin{aligned}noise&=12\\ power&=13\end{aligned}\end{equation*}']
        for region in regions:
            with self.subTest(region=region):
                self.assertEqual(learn._phrase_features('估计区间 '+region+' 保持稳定。'),
                                 learn._phrase_features('估计区间 保持稳定。'))

    def test_balanced_citations_references_and_link_targets_are_not_words(self):
        text = (r'We estimate \citep[see {nested}][p.~12]{Smith2024,Li2023} '
                r'\textcite*{王某2024} \Cref{fig:alpha} \label{sec:结果} '
                r'\url{https://example.test/data2024} \href{https://example.test/2025}{credible intervals}.')
        self.assertEqual(learn._phrase_features(text), learn._phrase_features('We estimate credible intervals.'))
        self.assertEqual(learn._phrase_features(r'We estimate \cites[see]{A2024}[p.~2]{B2025} intervals.'),
                         learn._phrase_features('We estimate intervals.'))

    def test_formatting_arguments_escaped_markers_and_ordinary_words_survive(self):
        self.assertEqual(learn._phrase_features(r'We \emph{estimate} \textbf{credible intervals}.'),
                         learn._phrase_features('We estimate credible intervals.'))
        self.assertEqual(learn._phrase_features(r'We pay \$12 and retain useful prose, at 8% efficiency.'),
                         learn._phrase_features('We pay and retain useful prose, at efficiency.'))
        self.assertIn('word:citep', learn._phrase_features('The citep command selects citations.'))
        self.assertIn('cjk2:标定', learn._phrase_features(r'我们采用\emph{独立标定}。'))

    def test_nested_note_groups_and_math_comments_do_not_leak_literals(self):
        self.assertEqual(learn._phrase_features(r'We report \citep[see {A]B}]{Key2024} intervals.'),
                         learn._phrase_features('We report intervals.'))
        regions = ['$noise % ignored $\n power$',
                   '\\begin{equation}noise % ignored \\end{equation}\n power\\end{equation}']
        for region in regions:
            self.assertEqual(learn._phrase_features('We estimate '+region+' intervals.'),
                             learn._phrase_features('We estimate intervals.'))

    def test_masked_regions_do_not_join_unrelated_fragments_or_swallow_malformed_prose(self):
        self.assertEqual(learn._phrase_features(r'甲$noise$乙'), [])
        self.assertEqual(learn._phrase_features(r'甲\cite{key}乙'), [])
        self.assertIn('word:retain', learn._phrase_features(r'Unclosed $alpha and retain prose.'))
        self.assertIn('word:retain', learn._phrase_features(r'Unclosed \cite{alpha and retain prose.'))
        self.assertIn('word:retain', learn._phrase_features(r'\begin{equation} alpha and retain prose.'))
        self.assertIn('cjk2:有效', learn._phrase_features(r'\begin{abstract}有效估计\end{abstract}'))

    def test_actual_old_numeric_weights_cannot_bias_a_new_candidate_pool(self):
        with closing(learn._open(self.db)) as conn, conn:
            conn.execute("INSERT INTO phrase_prefs VALUES ('results::support','word:12',1000,1,?)", (learn._now_iso(),))
        learn.set_syntax_backend(self.db,'off')
        baseline = self.rows()
        for count in (12,13):
            pool=[{'id':'long','label':'Neutral','text':f'对于本轮纳入检验的全部记录，未能发现漂移（n={count}）[8]。'},
                  {'id':'short','label':'Neutral','text':f'样本未见漂移（n={count}）[8]。'}]
            rows=learn.rank_candidates(self.db,'results','support',pool)
            self.assertEqual([row['score'] for row in rows],[0,0])
            self.assertEqual([row['id'] for row in rows],['long','short'])
        self.assertEqual(self.rows(),baseline)

    def test_author_choice_preserves_literals_and_learns_prose_once_across_restart(self):
        sentence = '我们估计 '+r'$\alpha=12$，并报告区间 \citep{Li2024}。'+'\r\n'
        receipt = self.choose(sentence)
        with closing(sqlite3.connect(self.db)) as conn:
            keys = {r[0] for r in conn.execute('SELECT feature FROM phrase_prefs')}
        self.assertIn('cjk2:估计',keys)
        self.assertFalse(keys & {'word:alpha','word:12','word:li2024','word:citep'})
        self.assertEqual(learn.list_exemplars(self.db,'author')[0]['text'],sentence)
        before=self.rows()
        fresh_spec=importlib.util.spec_from_file_location('fresh_literal_engine',ROOT/'library/learn.py')
        fresh=importlib.util.module_from_spec(fresh_spec);fresh_spec.loader.exec_module(fresh)
        self.assertEqual(fresh.choice_file(self.db,'results','support',self.file),{**receipt,'replayed':True})
        self.assertEqual(self.rows(),before)

    def test_clean_prose_preferences_transfer_with_different_literals_and_context(self):
        learn.set_syntax_backend(self.db,'off')
        self.choose(r'We estimate credible intervals $\alpha=12$ \citep{Li2024}.')
        pool=[{'id':'other','label':'Neutral','text':r'They obtain uncertainty bands $\theta=13$ \citep{Wang2025}.'},
              {'id':'preferred','label':'Neutral','text':r'We estimate credible intervals $\theta=13$ \citep{Wang2025}.'}]
        rows=learn.rank_candidates(self.db,'results','support',pool)
        self.assertEqual(rows[0]['id'],'preferred');self.assertGreater(rows[0]['phrase_score'],rows[1]['phrase_score'])
        changed=deepcopy(pool)
        for c in changed:c['text']=c['text'].replace('theta=13','kappa=145').replace('Wang2025','Zhou1999')
        self.assertEqual([(r['id'],r['phrase_score']) for r in rows],[(r['id'],r['phrase_score']) for r in learn.rank_candidates(self.db,'results','support',changed)])
        self.assertTrue(all(row['score']==0 for row in learn.rank_candidates(self.db,'methods','support',pool)))


if __name__=='__main__':unittest.main()
