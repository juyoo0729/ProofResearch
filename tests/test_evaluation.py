import importlib.util
import hashlib
from pathlib import Path

spec = importlib.util.spec_from_file_location('evaluation_run', Path(__file__).parents[1] / 'evaluation/run.py')
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def test_gold_never_enters_public_question_and_failed_runs_are_not_correct_holds():
    q = dict(tickers=['AMD'],as_of='2026-10-12',question='test',target={'kind':'comparison'},
             category='hold',expected={'status':'HOLD','reason_kind':'period_mismatch','secret_label':'gold'})
    assert 'expected' not in evaluation.public_question(q) and 'category' not in evaluation.public_question(q)
    record = dict(error=None,execution={'status':'failed','evidence_usable':True},
                  answer={'status':'HOLD','value':None,'reason':'기간이 다름'})
    assert not evaluation.grade(q,record)['correct']
    record['execution']['status']='completed'
    assert evaluation.grade(q,record)['correct']
    record['execution']['evidence_usable']=False
    assert not evaluation.grade(q,record)['correct']


def test_correct_number_with_wrong_unit_or_missing_citation_does_not_pass_all_metrics():
    q = dict(category='numeric',target={'ticker':'AMD','end':'2025-12-27'},
             expected={'value':'100','unit':'USD','tolerance':'0.5','required_ids':['cell']})
    r = dict(error=None,execution={'status':'completed','evidence_usable':True},
             answer={'status':'PASS','value':'100','unit':'%','citations':[]})
    assert not evaluation.grade(q,r)['correct']
    r['answer']['unit']='USD'
    assert evaluation.grade(q,r)['correct'] and not evaluation.grade(q,r)['citation_valid']
    r['answer']['citations']=[{'id':'cell','quote':'100'}]
    r['evidence']={'cell':{'quote':'100','ticker':'AMD','report_end':'2024-12-28'}}
    assert not evaluation.grade(q,r)['citation_valid']
    r['evidence']['cell']['report_end']='2025-12-27'
    assert evaluation.grade(q,r)['citation_valid']
    r['answer']['status']='HOLD'
    assert not evaluation.grade(q,r)['citation_valid']


def test_gold_split_and_independent_financial_inputs():
    gold=evaluation.read(evaluation.DATA/'gold.json')
    dev=[q for q in gold['questions'] if q['split']=='dev']
    final=[q for q in gold['questions'] if q['split']=='eval']
    assert len(dev)==20 and len(final)==40
    assert {k:sum(q['category']==k for q in final) for k in ('numeric','narrative','hold')} == {'numeric':24,'narrative':8,'hold':8}
    assert len({q['id'] for q in gold['questions']})==60
    dev_targets={(q['target']['ticker'],q['target']['end'],q['target']['metric']) for q in dev if q['category']=='numeric'}
    final_targets={(q['target']['ticker'],q['target']['end'],q['target']['metric']) for q in final if q['category']=='numeric'}
    assert not dev_targets & final_targets


def test_frozen_gold_and_corpus_keep_exact_bytes_after_checkout():
    lock = evaluation.read(evaluation.DATA / 'freeze.json')
    for name in ('gold', 'corpus'):
        body = (evaluation.DATA / f'{name}.json').read_bytes()
        assert hashlib.sha256(body).hexdigest() == lock[f'{name}_sha256']
