// Testa a junção de mudanças do financas.html (node --test tests/sync_merge.test.mjs)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const html = readFileSync(new URL('../financas.html', import.meta.url), 'utf8');
const trecho = html.match(/\/\* <merge> \*\/([\s\S]*?)\/\* <\/merge> \*\//)[1];
const SyncMerge = new Function(trecho + '; return SyncMerge;')();

const tx = (id, valor, extra = {}) => ({ id, data: '2026-10-01', descricao: id, valor, tipo: 'despesa', ...extra });
const ids = lista => lista.map(t => t.id);

test('o que a secretária lançou e o que foi lançado no app ficam os dois', () => {
  const base = { transacoes: [tx('a', 10)] };
  const local = { transacoes: [tx('a', 10), tx('app', 20)] };
  const remoto = { transacoes: [tx('a', 10), tx('secretaria', 30)] };
  assert.deepEqual(ids(SyncMerge.juntar(base, local, remoto).transacoes), ['a', 'secretaria', 'app']);
});

test('apagar no app vale; o que não foi mexido segue o Firebase', () => {
  const base = { transacoes: [tx('a', 10), tx('b', 20), tx('c', 30)] };
  const local = { transacoes: [tx('b', 20), tx('c', 30)] };                       // apagou "a" aqui
  const remoto = { transacoes: [tx('a', 10), tx('b', 25)] };                      // editou "b" e apagou "c" lá
  const final = SyncMerge.juntar(base, local, remoto).transacoes;
  assert.deepEqual(final, [tx('b', 25)]);
});

test('edição feita aqui vence, inclusive se lá apagaram', () => {
  const base = { transacoes: [tx('a', 10), tx('b', 20)] };
  const local = { transacoes: [tx('a', 11), tx('b', 21)] };
  const remoto = { transacoes: [tx('a', 99)] };                                   // editou "a", apagou "b"
  assert.deepEqual(SyncMerge.juntar(base, local, remoto).transacoes, [tx('a', 11), tx('b', 21)]);
});

test('ordem diferente das chaves (como o Firestore devolve) não conta como mudança', () => {
  const doFirebase = { valor: 10, tipo: 'despesa', id: 'a', descricao: 'a', data: '2026-10-01' };
  const base = { transacoes: [doFirebase] };
  const local = { transacoes: [tx('a', 10)] };                                    // mesmas chaves, outra ordem
  const remoto = { transacoes: [{ ...doFirebase, valor: 50 }] };
  assert.equal(SyncMerge.juntar(base, local, remoto).transacoes[0].valor, 50);
});

test('valores simples: muda quem mexeu', () => {
  const base = { metaInvest: 900, taxaInvest: 1 };
  assert.deepEqual(
    (({ metaInvest, taxaInvest }) => ({ metaInvest, taxaInvest }))(
      SyncMerge.juntar(base, { metaInvest: 1000, taxaInvest: 1 }, { metaInvest: 900, taxaInvest: 1.2 })),
    { metaInvest: 1000, taxaInvest: 1.2 });
});

test('itens sem id (fixosLog) se juntam sem repetir', () => {
  const pago = { fixoId: 'f1', mesAno: '10-2026' };
  const final = SyncMerge.juntar({ fixosLog: [] }, { fixosLog: [pago] },
                                 { fixosLog: [{ mesAno: '10-2026', fixoId: 'f1' }, { fixoId: 'f2', mesAno: '10-2026' }] });
  assert.equal(final.fixosLog.length, 2);
});

test('primeira sincronização: vale o Firebase, mas o que só existia aqui fica', () => {
  const local = { transacoes: [tx('velha', 1)], objCompras: [{ id: 'o1', nome: 'Tênis' }], metaInvest: 900 };
  const remoto = { transacoes: [tx('a', 10)], objCompras: null, metaInvest: 1000 };
  const final = SyncMerge.primeiraVez(local, remoto);
  assert.deepEqual(ids(final.transacoes), ['a']);
  assert.deepEqual(final.objCompras, [{ id: 'o1', nome: 'Tênis' }]);
  assert.equal(final.metaInvest, 1000);
});
