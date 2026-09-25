"""独立验收发现的缺陷：先运行本文件确认红测试，再修改实现。"""
import json
from datetime import date
import pytest
from kbqa.service import Service
from kbqa.live import LiveEngine
from kbqa.trace import Trace
from kbqa.timeparse import parse_time

@pytest.fixture
def svc(tmp_path, monkeypatch):
    for key in ('LLM_API_KEY', 'LLM_MODEL', 'LLM_BASE_URL'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('VAR_DIR', str(tmp_path))
    return Service()

def test_retrieval_identity_and_large_top_k(svc):
    hits = svc.retrieve('外卖订单多久内可以退款', 5)['results']
    chunks = {c.chunk_id: c for c in svc.index.chunks}
    for hit in hits:
        original = chunks[hit['chunk_id']]
        assert hit['doc_id'] == original.doc_id
        assert hit['text'] == original.text
    assert len(svc.retrieve('退款', len(chunks))['results']) == len(chunks)


def test_cross_document_reference_bridges_bilingual_attachment(svc):
    hits = svc.retrieve('三文鱼那次断供供应商赔了多少钱', 5)['results']
    assert any(hit['doc_id'] == 'KB-022' for hit in hits)

def test_filtered_padding_cannot_be_used_as_facts(svc):
    r = svc.retriever.search('退款', top_k=len(svc.index.chunks))
    assert len(r.hits) == len(svc.index.chunks)
    assert all(h.doc_id != 'KB-012' for h in r.ranked)
    assert all(h.padded for h in r.hits if h.doc_id == 'KB-012')

@pytest.mark.parametrize('q', ['S02 6月净营业额', 'S03 7月净营业额', 'P06 8月销量'])
def test_entity_digits_do_not_consume_month(q):
    spec = parse_time(q, date(2026,9,1))
    month = int(q.split()[1][0])
    assert spec.window[0] == f'2026-{month:02d}-01'

def engine(svc):
    return LiveEngine(None,svc.answerer,svc.run_tool,'2026-09-01',svc.data_period)

def test_live_binds_metric_to_field(svc):
    q='6月净营业额是多少？'
    result=svc.tools.query_metrics('2026-06-01','2026-06-30')
    evidence=[dict(tool='query_metrics',params=dict(start='2026-06-01',end='2026-06-30'),result=result)]
    a=engine(svc)._finalise(svc.planner.plan(q,[]),f'6月净营业额是{result["orders"]}元。',evidence,{},Trace('a',q))
    assert '净营业额是4311元' not in a.answer
    assert '156757' in a.answer and a.data_evidence

def test_live_does_not_treat_user_number_as_evidence(svc):
    q='2026年6月净营业额是999999元吗？'
    a=engine(svc)._finalise(svc.planner.plan(q,[]),'2026年6月净营业额是999999元。',[],{},Trace('a',q))
    assert '999999' not in a.answer
    assert a.answer_type == 'refusal' or a.data_evidence

def test_live_cannot_attach_contradictory_citation(svc):
    q='外卖订单多久内可以退款？'
    retrieved={'q':svc.retrieve(q)['results']}
    a=engine(svc)._finalise(svc.planner.plan(q,[]),'外卖订单可以无限期退款。[KB-013]',[],retrieved,Trace('a',q))
    assert '无限期' not in a.answer
    assert '24' in a.answer and a.citations

def test_live_historical_tool_uses_plan(svc):
    q='2026年5月外卖订单多久内可以退款？'
    p=svc.planner.plan(q,[])
    r=svc.run_tool('search_kb',{'query':'外卖退款期限'},plan=p,trace=Trace('a',q))
    ids={h['doc_id'] for h in r['results']}
    assert 'KB-012' in ids and 'KB-013' not in ids

def test_mock_tools_and_error_are_traced(svc,monkeypatch):
    r=svc.chat(None,'六月净营业额是多少？')
    tr=svc.get_trace(r['trace_id'])
    calls=[x['detail'] for x in tr['steps'] if x['step']=='tool']
    assert any(c.get('tool')=='query_metrics' and c.get('result',{}).get('net_revenue')==156757 for c in calls)
    def fail(*a,**kw):
        raise RuntimeError('AUDIT_INJECTED_TOOL_FAILURE')
    monkeypatch.setattr(svc.tools,'query_metrics',fail)
    r=svc.chat(None,'六月净营业额是多少？')
    assert r['answer_type']=='refusal'
    assert 'AUDIT_INJECTED_TOOL_FAILURE' in json.dumps(svc.get_trace(r['trace_id'])['errors'])

def test_daily_evidence_matches_display_and_limits(svc):
    r=svc.chat(None,'S02 六月每天营业额是多少？')
    assert r['answer_type']=='data'
    evidence=r['data_evidence'][0]
    assert len(evidence['result']['days'])==7
    assert evidence['params']['limit']==7
    assert evidence['result']['days_total']==30
    assert len(json.dumps(evidence['result']).encode())<4096
    assert '共 30 天' in r['answer']

def test_trace_preserves_entire_llm_exchange(monkeypatch):
    import httpx
    from kbqa.llm import LLMClient
    message={'role':'assistant','content':'回复'*3000,'reasoning_content':'reason','tool_calls':[]}
    monkeypatch.setattr(httpx,'post',lambda *a,**kw:httpx.Response(200,json={'choices':[{'message':message,'finish_reason':'stop'}]}))
    tr=Trace('a','q'); messages=[{'role':'user','content':'问题'*3000}]
    tools=[{'type':'function','function':{'name':'test'}}]
    LLMClient('http://localhost','fake','fake').chat(messages,tools,on_call=tr.llm)
    call=tr.llm_calls[0]
    assert call['request']['messages']==messages
    assert call['request']['tools']==tools
    assert call['response']['choices'][0]['message']==message


def test_response_limits_fail_closed():
    from kbqa.contracts import enforce_limits
    from kbqa.schemas import Answer
    tr=Trace('limit','q')
    too_big=Answer('数据很多','data',data_evidence=[{'tool':'example','params':{},'result':{'values':list(range(61))}}])
    safe=enforce_limits(too_big,tr)
    assert safe.answer_type=='refusal' and not safe.data_evidence
    assert tr.steps[-1]['step']=='response_limit'


def test_model_cannot_choose_unretrieved_or_obsolete_citation(svc):
    q='外卖订单多久内可以退款？'
    a=engine(svc)._finalise(svc.planner.plan(q,[]),'7天内都可退款。[KB-012]',[],
        {'q':[{'doc_id':'KB-012','chunk_id':'KB-012#1','score':100}]},Trace('a',q))
    assert all(c['doc_id']!='KB-012' for c in a.citations)
    assert '24' in a.answer
