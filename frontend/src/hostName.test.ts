/**
 * Имя хоста для глаз (18.11.2026): метки `xn--…` раскодированы, остальное — как было; что не
 * раскодировать — показать как есть. Векторы разбора — RFC 3492, раздел 7.1; имена хостов
 * сверены с `idna` питона.
 */

import { describe, expect, it } from 'vitest';

import { decodePunycode, nameIncludes, shownAddress, shownHost } from './hostName';

describe('разбор Punycode: векторы RFC 3492, раздел 7.1', () => {
  it.each([
    ['(A) арабский', 'egbpdaj6bu4bxfgehfvwxn', 'ليهمابتكلموشعربي؟'],
    ['(B) китайский упрощённый', 'ihqwcrb4cv8a8dqg056pqjye', '他们为什么不说中文'],
    ['(C) китайский традиционный', 'ihqwctvzc91f659drss3x8bo0yb', '他們爲什麽不說中文'],
    ['(D) чешский', 'Proprostnemluvesky-uyb24dma41a', 'Pročprostěnemluvíčesky'],
    ['(E) иврит', '4dbcagdahymbxekheh6e0a7fei0b', 'למההםפשוטלאמדבריםעברית'],
    ['(F) хинди', 'i1baa7eci9glrd9b2ae1bj0hfcgg6iyaf8o0a1dig0cd', 'यहलोगहिन्दीक्योंनहींबोलसकतेहैं'],
    [
      '(G) японский',
      'n8jok5ay5dzabd5bym9f0cm5685rrjetr6pdxa',
      'なぜみんな日本語を話してくれないのか',
    ],
    [
      '(H) корейский',
      '989aomsvi5e83db1d2a355cv1e0vak1dwrv93d5xbh15a0dt30a5jpsd879ccm6fea98c',
      '세계의모든사람들이한국어를이해한다면얼마나좋을까',
    ],
    // Заглавная «D» — пометка регистра (раздел 3.5): цифра та же, что «d».
    ['(I) русский', 'b1abfaaepdrnnbgefbaDotcwatmq2g4l', 'почемужеонинеговорятпорусски'],
    [
      '(J) испанский',
      'PorqunopuedensimplementehablarenEspaol-fmd56a',
      'PorquénopuedensimplementehablarenEspañol',
    ],
    [
      '(K) вьетнамский',
      'TisaohkhngthchnitingVit-kjcr8268qyxafd2f1b9g',
      'TạisaohọkhôngthểchỉnóitiếngViệt',
    ],
    ['(L) японский с латиницей', '3B-ww4c5e180e575a65lsy2b', '3年B組金八先生'],
    [
      '(M) дефисы в ASCII',
      '-with-SUPER-MONKEYS-pc58ag80a8qai00g7n9n', // pragma: allowlist secret
      '安室奈美恵-with-SUPER-MONKEYS',
    ],
    [
      '(N) двойной дефис',
      'Hello-Another-Way--fc4qua05auwb3674vfr0b', // pragma: allowlist secret
      'Hello-Another-Way-それぞれの場所',
    ],
    ['(O) цифра в конце', '2-u9tlzr9756bt3uc0v', 'ひとつ屋根の下2'],
    ['(P) вперемешку', 'MajiKoi5-783gue6qz075azm5e', 'MajiでKoiする5秒前'],
    ['(Q) латиница посередине', 'de-jg4avhby1noc0d', 'パフィーdeルンバ'],
    ['(R) без ASCII', 'd9juau41awczczp', 'そのスピードで'],
    ['(S) только ASCII', '-> $1.00 <--', '-> $1.00 <-'],
  ])('%s', (_name, encoded, decoded) => {
    expect(decodePunycode(encoded)).toBe(decoded);
  });

  it.each([
    ['не цифра', 'e1afmkf_d'],
    ['обрыв числа', '99'],
    // Восьмая «9» подряд уводит счёт за 2³¹ − 1.
    ['переполнение счёта', '99999999a'],
    ['не ASCII до дефиса', 'пример-abc'],
  ])('сбой — `null`, а не половина метки: %s', (_name, encoded) => {
    expect(decodePunycode(encoded)).toBeNull();
  });
});

describe('хост для глаз', () => {
  it.each([
    ['xn--e1afmkfd.xn--p1ai', 'пример.рф'],
    ['XN--E1AFMKFD.XN--P1AI', 'пример.рф'],
    ['Xn--E1afmkfd.xN--P1ai', 'пример.рф'],
    ['news.xn--e1afmkfd.xn--p1ai', 'news.пример.рф'],
    ['xn--80ai1b.example.test', 'сад.example.test'],
    ['xn--hxajbheg2az3al.example', 'παράδειγμα.example'],
    ['xn--mgbh0fb.example', 'مثال.example'],
    ['xn--fsqu00a.example', '例子.example'],
    ['xn--zckzah.example', 'テスト.example'],
    ['xn--strae-oqa.example', 'straße.example'],
    ['xn--e1afmkfd.xn--p1ai.', 'пример.рф.'],
  ])('%s — %s', (host, shown) => {
    expect(shownHost(host)).toBe(shown);
  });

  it.each([
    ['ASCII-имя', 'quiet-blog.example'],
    ['ASCII-имя с заглавными — регистр не меняется', 'News.Example.TEST'],
    ['уже юникодное', 'пример.рф'],
    ['уже юникодное с заглавными', 'Пример.РФ'],
    ['пустое', ''],
  ])('как есть: %s', (_name, host) => {
    expect(shownHost(host)).toBe(host);
  });

  it.each([
    ['битая метка — вся строка, а не половина', 'xn--e1afmkf_d.xn--p1ai'],
    ['обрыв числа', 'xn--99.example'],
    ['переполнение счёта', 'xn--99999999a.example'],
    ['раскодировалось в ASCII — другое имя на вид', 'xn--abc-.example'],
    ['латиница с кириллической «а» — подделка', 'xn--exmple-4nf.test'],
    ['адрес страницы — не имя хоста', 'https://xn--e1afmkfd.xn--p1ai/page'],
    ['метка длиннее 63 знаков', `xn--${'a'.repeat(60)}.example`],
    ['одна приставка', 'xn--.example'],
  ])('не раскодировать — как есть: %s', (_name, host) => {
    expect(shownHost(host)).toBe(host);
  });

  it('`null` — как есть', () => {
    expect(shownHost(null)).toBeNull();
  });
});

describe('адрес почты для глаз', () => {
  it.each([
    ['info@xn--e1afmkfd.xn--p1ai', 'info@пример.рф'],
    ['INFO@XN--80AI1B.example.test', 'INFO@сад.example.test'],
    // Имя ящика — не хост: `xn--` в нём не трогается.
    ['xn--80ai1b@xn--e1afmkfd.xn--p1ai', 'xn--80ai1b@пример.рф'],
    ['"a@b"@xn--e1afmkfd.xn--p1ai', '"a@b"@пример.рф'],
    // Без «@» — имя хоста: в колонке стоп-листа бывает и домен.
    ['xn--e1afmkfd.xn--p1ai', 'пример.рф'],
    ['editor@quiet-blog.example', 'editor@quiet-blog.example'],
  ])('%s — %s', (email, shown) => {
    expect(shownAddress(email)).toBe(shown);
  });

  it('`null` — как есть', () => {
    expect(shownAddress(null)).toBeNull();
  });
});

describe('поиск по имени — в обеих записях', () => {
  it.each([
    ['xn--e1afmkfd.xn--p1ai', 'пример', true],
    ['xn--e1afmkfd.xn--p1ai', 'ПРИМЕР.рф', true],
    ['xn--e1afmkfd.xn--p1ai', 'xn--e1a', true],
    ['info@xn--80ai1b.example.test', 'сад', true],
    ['info@xn--80ai1b.example.test', 'INFO', true],
    ['quiet-blog.example', 'пример', false],
  ])('%s, «%s» — %s', (name, needle, found) => {
    expect(nameIncludes(name, needle)).toBe(found);
  });
});
