/**
 * Смета на экране прогона: что будет куплено и во что обойдётся.
 *
 * Числа сметы считает сервер, экран их только называет — и проверяется здесь,
 * что называет он то, что посчитано: ключи, которые купит выдача, долю дублей,
 * которой посчитаны домены, цену выдачи (и в доли цента) и бюджет.
 */

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { FITS, openRun } from '../test/runFixtures';

const TOO_MUCH = { ...FITS, units_left: 100, budget: 100, affordable: false, shortfall: 77 };

const CAP_EATEN = {
  ...FITS,
  units_spent_this_month: 99_950,
  cap_left: 50,
  budget: 50,
  affordable: false,
  shortfall: 127,
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
    // вторая статья после Ahrefs.
    expect(await screen.findByText(/Потрачено нами юнитов с начала месяца/)).toBeInTheDocument();
    expect(screen.getByText(/0,72\s\$/)).toBeInTheDocument();
    expect(screen.getByText(/Выдача будет стоить примерно/)).toBeInTheDocument();
  });

  it('выдача в доли цента — «меньше 0,01 $», а не «примерно 0,00 $»', async () => {
    // Проверка прода 10.10.2026: 0,0024 $ округлялись в «0,00 $» — будто даром.
    await openRun({ 'POST /api/runs/estimate': { body: { ...FITS, serp_cost_usd: 0.0024 } } });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    const line = (await screen.findByText(/Выдача будет стоить/)).closest('p') as HTMLElement;
    expect(line).toHaveTextContent(/Выдача будет стоить меньше 0,01\s\$/);
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
        body: { ...FITS, run_ceiling: 5000, budget: 5000 },
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

  it('не помещается — кнопка не нажимается и сказано, чего не хватает', async () => {
    await openRun({ 'POST /api/runs/estimate': { body: TOO_MUCH } });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    expect(await screen.findByText('Не помещается в бюджет')).toBeInTheDocument();
    expect(screen.getByText(/Не хватает 77 юнитов/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Запустить/ })).toBeDisabled();
  });
});
