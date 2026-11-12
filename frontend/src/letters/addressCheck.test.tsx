/**
 * Проверка адреса перед письмом на экране «Письма» (11.11.2026): строка «ждут проверки
 * адреса», ступень воронки «адрес проверен» и итог сборки про адреса. Числа — словами сервера;
 * не прислал (проверка этапа выключена) — экран о проверке молчит.
 */

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { renderWith } from '../test/render';
import { buildCheckLine, checkCounts, checkedHint } from './addressCheck';
import { CheckLine } from './CheckLine';
import { EmptyQueue } from './EmptyQueue';
import { funnelSteps, stopOf, stopWords } from './targets';

/** Слова под воронкой, когда ноль — на звене «адрес проверен» (12.11.2026). */
const NOT_CHECKED =
  'Адреса ещё не проверялись: проверка идёт при сборке очереди — нажмите «Собрать очередь».';

const NBSP = ' ';

const ADVERTISERS = {
  рекламодателей: 2000,
  'со ссылкой': 1900,
  'цена донора свежая': 1800,
  'с адресом': 1500,
  'вне стоп-листа': 1400,
  'ещё не писали': 1300,
};

const CHECK = {
  'адрес проверен': 250,
  'не проверить': 12,
  'адрес не существует': 5,
  'ждут проверки адреса': 1050,
};

describe('числа проверки адреса', () => {
  it('сервер их не прислал — проверки на экране нет', () => {
    expect(checkCounts(ADVERTISERS)).toBeNull();
    expect(checkedHint(ADVERTISERS)).toBe('');
    const { container } = renderWith(<CheckLine funnel={ADVERTISERS} />);
    expect(container.querySelector('.addressCheckLine')).toBeNull();
  });

  it('«адрес проверен у N» продолжает строку «Ещё не писали», а нуля не говорит', () => {
    expect(checkedHint({ ...ADVERTISERS, ...CHECK })).toBe(` · адрес проверен у${NBSP}250`);
    expect(checkedHint({ ...ADVERTISERS, 'адрес проверен': 0 })).toBe('');
  });

  it('строка говорит, сколько ждут проверки, и число не отрывается от подписи', () => {
    renderWith(<CheckLine funnel={{ ...ADVERTISERS, ...CHECK }} />);

    const line = screen.getByText(/ждут проверки адреса:/);
    expect(line.textContent).toBe(`ждут проверки адреса:${NBSP}1${NBSP}050`);
    // Строкой текста искать нельзя: поиск схлопывает неразрывный пробел в обычный.
    expect(screen.getByText(/не проверить:/).textContent).toBe(
      `· не проверить:${NBSP}12 · адрес не существует:${NBSP}5`,
    );
  });

  it('ждущих нет — строка только о том, чего проверка не смогла', () => {
    renderWith(
      <CheckLine funnel={{ ...ADVERTISERS, 'адрес проверен': 1300, 'не проверить': 4 }} />,
    );

    expect(screen.queryByText(/ждут проверки адреса/)).toBeNull();
    expect(screen.getByText(/не проверить:/).textContent).toBe(`не проверить:${NBSP}4`);
  });

  it('все проверены и сказать нечего — строки нет', () => {
    const { container } = renderWith(
      <CheckLine funnel={{ ...ADVERTISERS, 'адрес проверен': 1300 }} />,
    );
    expect(container.querySelector('.addressCheckLine')).toBeNull();
  });

  it('что значат числа — в «i», только о тех, что стоят в строке', async () => {
    renderWith(
      <CheckLine
        funnel={{ ...ADVERTISERS, 'адрес проверен': 0, ...{ 'ждут проверки адреса': 1300 } }}
      />,
    );
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Проверка адреса перед письмом' }));

    const card = await screen.findByRole('dialog', { name: 'Проверка адреса перед письмом' });
    expect(card).toHaveTextContent('Проверка идёт при сборке очереди');
    expect(card).not.toHaveTextContent('Не проверить');
    expect(card).not.toHaveTextContent('Адрес не существует');
  });
});

describe('ступень воронки «адрес проверен»', () => {
  it('стоит последней у доноров, рекламодателей и бизнесов ниши, когда сервер её прислал', () => {
    const steps = funnelSteps({ ...ADVERTISERS, ...CHECK }, 'advertisers');
    expect(steps.at(-1)).toMatchObject({ step: 'адрес проверен', count: 250 });
    expect(funnelSteps({ подходящих: 3, 'адрес проверен': 1 }, 'donors').at(-1)?.step).toBe(
      'адрес проверен',
    );
    // Бизнесам ниши сервер шлёт её с 12.11.2026: адрес у них проверяется тем же правилом.
    const niche = { 'бизнесов ниши': 9, 'ещё не писали': 4, 'адрес проверен': 1 };
    expect(funnelSteps(niche, 'niche').at(-1)).toMatchObject({ step: 'адрес проверен', count: 1 });
  });

  it('без неё воронка та же, что до проверки', () => {
    expect(funnelSteps(ADVERTISERS, 'advertisers').map((one) => one.step)).toEqual(
      Object.keys(ADVERTISERS),
    );
  });

  it('ноль на ней — «ещё не проверяли», а не отсев: слова свои, без «Кончились»', () => {
    const steps = funnelSteps({ ...ADVERTISERS, 'адрес проверен': 0 }, 'advertisers');
    expect(stopOf(steps)?.step).toBe('адрес проверен');
    expect(stopWords(stopOf(steps))).toBe(NOT_CHECKED);

    renderWith(
      <EmptyQueue
        funnel={{ ...ADVERTISERS, 'адрес проверен': 0 }}
        target="advertisers"
        stale={false}
      />,
    );
    expect(screen.getByText(NOT_CHECKED)).toBeInTheDocument();
    expect(screen.queryByText(/Кончились/)).toBeNull();
  });

  it('у доноров слова те же', () => {
    renderWith(
      <EmptyQueue
        funnel={{ подходящих: 9, принятых: 7, 'с адресом': 5, 'адрес проверен': 0 }}
        target="donors"
        stale={false}
      />,
    );
    expect(screen.getByText(NOT_CHECKED)).toBeInTheDocument();
  });

  it('ноль ступенью раньше — прежнее «Кончились на ступени…»: адресатов и правда нет', () => {
    const none = { ...ADVERTISERS, 'ещё не писали': 0, 'адрес проверен': 0 };
    renderWith(<EmptyQueue funnel={none} target="advertisers" stale={false} />);

    expect(
      screen.getByText('Кончились на ступени «ещё не писали»: написаны все.'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Адреса ещё не проверялись/)).toBeNull();
  });
});

describe('итог сборки про адреса', () => {
  it('говорит, чем кончилась проверка, в порядке «вышло — не вышло»', () => {
    const said = buildCheckLine({
      prepared: 41,
      address_check: {
        'не существует': 5,
        существует: 31,
        'проверить нельзя': 4,
        'проверены раньше': 6,
      },
      unchecked: null,
    });
    expect(said).toBe(
      `Адреса перед письмом: существует${NBSP}31, проверены раньше${NBSP}6, проверить нельзя${NBSP}4, не существует${NBSP}5.`,
    );
  });

  it('проверка не состоялась — причина словами: письма этим адресам не собраны', () => {
    const said = buildCheckLine({
      address_check: { существует: 2, 'не проверено': 9 },
      unchecked: 'провайдер не ответил (HTTP 503)',
    });
    expect(said).toBe(
      `Адреса перед письмом: существует${NBSP}2, не проверено${NBSP}9. Непроверенным письма не собраны — провайдер не ответил (HTTP 503).`,
    );
  });

  it('проверки не было — строки нет', () => {
    expect(buildCheckLine({ prepared: 3 })).toBeNull();
    expect(buildCheckLine({ prepared: 3, address_check: {}, unchecked: null })).toBeNull();
  });
});
