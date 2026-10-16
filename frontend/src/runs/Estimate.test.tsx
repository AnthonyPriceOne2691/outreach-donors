/**
 * Смета на экране прогона: что будет куплено и во что обойдётся.
 *
 * Числа сметы считает сервер, экран их только называет — и проверяется здесь,
 * что называет он то, что посчитано: ключи, которые купит выдача, долю дублей,
 * которой посчитаны домены, цену выдачи (и в доли цента) и бюджет. Кап — на цикл ключа Ahrefs
 * (16.10.2026): период назван словами, а неизвестный размер ключа — причиной, не нулём.
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { ADMIN } from '../test/fixtures';
import { FITS, openRun } from '../test/runFixtures';

// Свободно у Ahrefs меньше остатка по капу — упор в лимит ключа.
const TOO_MUCH = {
  ...FITS,
  units_left: 100,
  budget: 100,
  affordable: false,
  shortfall: 77,
  binding: 'key' as const,
  short_of: ['key' as const],
};

const CAP_EATEN = {
  ...FITS,
  units_spent_in_cycle: 99_950,
  cap_left: 50,
  budget: 50,
  affordable: false,
  shortfall: 127,
  binding: 'cap' as const,
  short_of: ['cap' as const],
};

describe('смета', () => {
  it('доля дублей — та, которой посчитана смета, а не зашитое число', async () => {
    // Проверка прода 10.10.2026: «83% схлопывается в дубли» стояло от константы,
    // убранной 24.09, рядом с «≈ 31» из 40 результатов — то есть 22% дублей.
    await openRun({
      'POST /api/runs/estimate': {
        body: { ...FITS, expected_results: 40, expected_domains: 31, unique_share: 0.775 },
      },
    });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    expect(await screen.findByText('≈ 31')).toBeInTheDocument();
    expect(screen.getByText('22% схлопывается в дубли — по замеру')).toBeInTheDocument();
  });

  it('повторы ключей смета не считает и говорит об этом', async () => {
    // Проверка прода 10.10.2026: два одинаковых ключа и вариант с заглавными были
    // «3 ключа», а выдача покупала два. Сервер сводит повторы правилом выдачи и
    // считает два; под четырьмя строками поля это названо, а не прячется.
    await openRun({ 'POST /api/runs/estimate': { body: FITS } });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nремонт\nРемонт\nдизайн');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    expect(
      await screen.findByText('2 ключа × 10 результатов · 2 повтора не в счёт'),
    ).toBeInTheDocument();
  });

  it('смета называет стоимость выдачи — это другой счёт, не юниты', async () => {
    await openRun({ 'POST /api/runs/estimate': { body: { ...FITS, serp_cost_usd: 0.72 } } });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    // До этого среза расход на выдачу не показывался нигде, хотя это
    // вторая статья после Ahrefs. Наш расход юнитов — за цикл ключа, с его обнуления.
    expect(
      await screen.findByText(/Потрачено нами юнитов за цикл ключа, с 21 октября/),
    ).toBeInTheDocument();
    expect(screen.getByText(/0,72\s\$/)).toBeInTheDocument();
    expect(
      screen.getByText(/Выдача придёт за секунды и будет стоить примерно/),
    ).toBeInTheDocument();
  });

  it('выдача в доли цента — «меньше 0,01 $», а не «примерно 0,00 $»', async () => {
    // Проверка прода 10.10.2026: 0,0024 $ округлялись в «0,00 $» — будто даром.
    await openRun({ 'POST /api/runs/estimate': { body: { ...FITS, serp_cost_usd: 0.0024 } } });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    const line = (await screen.findByText(/Выдача придёт/)).closest('p') as HTMLElement;
    expect(line).toHaveTextContent(/будет стоить меньше 0,01\s\$/);
    expect(line).not.toHaveTextContent(/0,00/);
    expect(line).not.toHaveTextContent(/примерно/);
  });

  it('бюджет считается от остатка по капу, а не от самого капа', async () => {
    await openRun({ 'POST /api/runs/estimate': { body: CAP_EATEN } });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    expect(await screen.findByText(/по капу 50 из 100\s000/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Запустить/ })).toBeDisabled();
  });

  it('со своим потолком бюджет считается по нему, а не по капу', async () => {
    await openRun({
      'POST /api/runs/estimate': {
        body: { ...FITS, run_ceiling: 5000, budget: 5000, binding: 'ceiling' },
      },
    });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.type(screen.getByLabelText('Потолок юнитов'), '5000');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    // Свой потолок — про один прогон, кап — про месяц. Живая проверка
    // поймала ровно эту путаницу: месячная трата вычиталась из потолка.
    expect(await screen.findByText(/ваш потолок 5\s000/)).toBeInTheDocument();
    expect(screen.getByText(/по капу 93\s584 из 100\s000/)).toBeInTheDocument();
  });

  it.each([
    ['с правом «править пороги» — поднять самому', ADMIN, 'поднимите кап на экране «Расход».'],
    [
      'без права — попросить того, у кого оно есть',
      { ...ADMIN, permissions: ADMIN.permissions.filter((right) => right !== 'settings') },
      'попросите администратора поднять кап на экране «Расход».',
    ],
  ])('упор в кап — кап поднимают на «Расходе», ссылка туда: %s', async (_, me, words) => {
    // Решение владельца 16.10.2026: кап меняют на экране «Расход». Совет «поднимите кап»
    // без места, где это сделать, человеку не помогал — кап жил в окружении сервера.
    await openRun({
      'GET /api/auth/me': { body: me },
      'POST /api/runs/estimate': { body: CAP_EATEN },
    });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    const alert = (await screen.findByText('Не помещается в кап')).closest(
      '.mantine-Alert-root',
    ) as HTMLElement;
    expect(alert).toHaveTextContent('По нашему капу осталось 50 из 100 000 до обнуления 21 ноября');
    expect(alert).toHaveTextContent(words);
    expect(within(alert).getByRole('link', { name: '«Расход»' })).toHaveAttribute('href', '/usage');
  });

  it('без даты обнуления — с первого числа, и сказано почему', async () => {
    await openRun({
      'POST /api/runs/estimate': {
        body: { ...FITS, cycle_by_key: false, cycle_start: '2026-10-01T00:00:00+00:00' },
      },
    });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    expect(
      await screen.findByText(/с 1 октября — Ahrefs не назвал даты обнуления ключа/),
    ).toBeInTheDocument();
  });

  it('размер ключа неизвестен — словами причины, а не «по капу 0 из 0»', async () => {
    const unknown = 'Ahrefs ещё не ответил — размер ключа неизвестен';
    await openRun({
      'POST /api/runs/estimate': {
        body: {
          ...TOO_MUCH,
          units_cap: null,
          cap_left: 0,
          budget: 0,
          shortfall: 177,
          cap_unknown: unknown,
        },
      },
    });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    expect(await screen.findByText('Кап неизвестен')).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`кап неизвестен: ${unknown}`))).toBeInTheDocument();
    expect(screen.queryByText(/по капу 0/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Не помещается/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Запустить/ })).toBeDisabled();
  });

  it('не хватает ключа Ahrefs — увеличить лимит ключа, кап не поможет', async () => {
    // 16.10.2026: смета советовала «поднимите кап», когда кончался ключ, и кап поднимали
    // зря — бюджет всё равно держал остаток ключа.
    await openRun({ 'POST /api/runs/estimate': { body: TOO_MUCH } });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    const alert = (
      await screen.findByText('Не помещается: не хватает лимита ключа Ahrefs')
    ).closest('.mantine-Alert-root') as HTMLElement;
    expect(alert).toHaveTextContent('Не хватает 77 юнитов.');
    expect(alert).toHaveTextContent(
      'На ключе Ahrefs свободно 100 до обнуления 21 ноября — увеличьте лимит ключа в самом Ahrefs: кап здесь не поможет.',
    );
    expect(within(alert).queryByRole('link', { name: '«Расход»' })).toBeNull();
    expect(
      screen.getByText(/^упор — у Ahrefs 100 · по капу 93\s584 из 100\s000$/),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Запустить/ })).toBeDisabled();
  });

  it('не хватает и ключа, и капа — проверить оба, каждый своей строкой', async () => {
    // Слово владельца 16.10.2026: «если и то, и другое — проверьте лимиты ключа и лимиты капа».
    const both = { ...TOO_MUCH, cap_left: 50, short_of: ['key', 'cap'] };
    await openRun({ 'POST /api/runs/estimate': { body: both } });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    const alert = (
      await screen.findByText('Не помещается: проверьте лимит ключа Ahrefs и кап')
    ).closest('.mantine-Alert-root') as HTMLElement;
    expect(alert).toHaveTextContent('увеличьте лимит ключа в самом Ahrefs.');
    expect(alert).not.toHaveTextContent('кап здесь не поможет');
    expect(alert).toHaveTextContent('По нашему капу осталось 50 из 100 000 до обнуления 21 ноября');
    expect(within(alert).getByRole('link', { name: '«Расход»' })).toHaveAttribute('href', '/usage');
  });

  it.each([
    [
      'пространство Ahrefs — общий лимит всех ключей',
      { binding: 'workspace', short_of: ['workspace'] },
      'Не помещается: не хватает лимита пространства Ahrefs',
      'это общий лимит всех его ключей; увеличьте его в самом Ahrefs: кап здесь не поможет.',
    ],
    [
      'свой потолок прогона — поднять или убрать в форме',
      { units_left: 100_000, binding: 'ceiling', run_ceiling: 100, short_of: ['ceiling'] },
      'Не помещается в ваш потолок',
      'Потолок этого прогона — 100: поднимите или уберите его в форме.',
    ],
  ])('не хватает — %s', async (_, extra, title, words) => {
    await openRun({ 'POST /api/runs/estimate': { body: { ...TOO_MUCH, ...extra } } });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    const alert = (await screen.findByText(title)).closest('.mantine-Alert-root') as HTMLElement;
    expect(alert).toHaveTextContent(words);
  });

  it('на счету DataForSEO мало — «пополните счёт», зелёного «помещается» нет, кнопка стоит', async () => {
    // Слово владельца 16.10.2026: на счету меньше $5 или выдача дороже остатка — прогон не
    // начинается, и человек видит, что пополнить.
    const words =
      'На счету DataForSEO 3,20 $ — меньше 5,00 $: пополните счёт DataForSEO. До пополнения прогоны не запускаются.';
    await openRun({
      'POST /api/runs/estimate': {
        body: { ...FITS, serp_balance_usd: 3.2, serp_refusal: words },
      },
    });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    const alert = (await screen.findByText('Пополните счёт DataForSEO')).closest(
      '.mantine-Alert-root',
    ) as HTMLElement;
    expect(alert).toHaveTextContent(words);
    expect(screen.queryByText('Помещается')).not.toBeInTheDocument();
    expect(screen.getByText(/Выдача придёт/).closest('p')).toHaveTextContent(
      /На счету DataForSEO 3,20\s\$/,
    );
    expect(screen.getByRole('button', { name: /Запустить/ })).toBeDisabled();
  });

  it('на счету хватает — остаток назван рядом с ценой выдачи, запуск открыт', async () => {
    await openRun({
      'POST /api/runs/estimate': { body: { ...FITS, serp_balance_usd: 12.3 } },
    });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    const line = (await screen.findByText(/Выдача придёт/)).closest('p') as HTMLElement;
    expect(line).toHaveTextContent(/На счету DataForSEO 12,30\s\$/);
    expect(screen.getByText('Помещается')).toBeInTheDocument();
    expect(screen.queryByText('Пополните счёт DataForSEO')).not.toBeInTheDocument();
  });
});
