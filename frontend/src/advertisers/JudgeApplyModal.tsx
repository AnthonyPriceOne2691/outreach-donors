/**
 * Окно «Применить советы судьи»: полоса, план, что произойдёт, ход по порциям, итог и партии.
 *
 * **План — до единого решения и всегда свежий** (`crawl/judge_plan.py`): сколько пар и доноров,
 * какими правилами судья так сказал и примеры — жребием, а не верхом очереди. Запускать нечего — окно
 * всё равно открывается: «пар с таким советом нет» и список партий с откатом отвечают на вопросы
 * лучше выключенной кнопки.
 *
 * **Что произойдёт — на виду, коротко и честно** (`docs/UI_RULES.md`, 10.11.2026: последствие
 * действия не прячут). «Не пишем»: пары уходят из очереди и остаются под «Показывать решённые».
 * «Пишем» само ничего не отправляет: рекламодателем домен делает «Перевести», письма собирает
 * «Собрать» — окно называет это словами, а не обещает письма.
 *
 * **Ход — порциями** (`useJudgeApplyRun`): «Применяем: 80 из 120», «Остановить» встаёт после
 * летевшей порции. Пока порции идут, окно не закрыть ни крестиком, ни мимо: молчаливое закрытие
 * выглядело бы как «применилось всё».
 *
 * **В окне одна прокрутка** (`oneScroll` в glass.css): пояснение, переключатель полосы и кнопки
 * стоят, прокручиваются план, итог и партии. Длинные списки — правила, примеры, партии — свёрнуты с
 * числом.
 */

import {
  Alert,
  Button,
  Group,
  List,
  Loader,
  Modal,
  SegmentedControl,
  Stack,
  Text,
} from '@mantine/core';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';

import { fetchJudgePlan, JUDGE_APPLY_KEY } from '../api/advertisersJudgeApply';
import type { JudgeBand, JudgePlan, JudgeStanding } from '../api/advertisersJudgeApply';
import { refusalOf } from '../api/client';
import { ACTIONS_GAP } from '../components/formRhythm';
import { formatNumber, plural } from '../format';
import { JudgeBatches } from './JudgeBatches';
import { BAND_WORDS, counted } from './judgeApplyWords';
import { useJudgeApplyRun } from './useJudgeApplyRun';
import type { ApplyRun } from './useJudgeApplyRun';

/** Очередь проверки и всё под её ключом: строки, числа советов, план и партии. */
const QUEUE_KEY = ['advertisers'] as const;
/** Сколько правил и примеров на виду; остальные — за «Показать все · N». */
const FOLDED = 5;
/** Сколько ответивших доменов названо поимённо в итоге. */
const ANSWERED_SHOWN = 5;

const INTRO =
  'Решения лягут сразу всей полосе — тем же путём, что «Пишем» и «Не пишем» у строки. «Не понять» ' +
  'остаётся человеку.';

/** Форма — тем же столбиком, что тело окна (`oneScroll`): середина забирает остаток рамки. */
const COLUMN: CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  flex: '1 1 auto',
  minHeight: 0,
  gap: 'var(--mantine-spacing-sm)',
};

/** Какая полоса открыта первой: «не пишем», а если её нет — «пишем». */
function firstBand(standing: JudgeStanding | undefined): JudgeBand {
  return standing !== undefined && standing.skip === 0 && standing.write > 0 ? 'write' : 'skip';
}

/** Список, свёрнутый до первых строк, с числом на кнопке и «Свернуть» на том же месте. */
function Folded({ name, rows }: { name: string; rows: ReactNode[] }) {
  const [all, setAll] = useState(false);
  const shown = all ? rows : rows.slice(0, FOLDED);
  return (
    <Stack gap={4}>
      <List size="sm" spacing={4} aria-label={name}>
        {shown}
      </List>
      {rows.length > FOLDED ? (
        <Button
          variant="subtle"
          size="compact-sm"
          className="press"
          aria-expanded={all}
          style={{ alignSelf: 'flex-start' }}
          onClick={() => setAll(!all)}
        >
          {all ? 'Свернуть' : `Показать все · ${formatNumber(rows.length)}`}
        </Button>
      ) : null}
    </Stack>
  );
}

/** Что произойдёт у «не пишем». */
function SkipEffects({ plan }: { plan: JudgePlan }) {
  return (
    <>
      <Text size="sm">
        Пары уйдут из очереди и останутся под «Показывать решённые»: любую можно переиграть, партию
        — откатить ниже.
      </Text>
      {plan.takes_off > 0 && (
        <Alert color="yellow" title="Снимутся рекламодатели">
          У {counted('доменов:', plan.takes_off)} — рекламодатель и его письма в очереди: других
          подходящих ссылок у них нет.
        </Alert>
      )}
    </>
  );
}

/** Что произойдёт у «пишем»: честно — письма сами не уйдут. */
function WriteEffects({ plan }: { plan: JudgePlan }) {
  const launch = plan.launch;
  return (
    <List size="sm" spacing={4} aria-label="Что произойдёт">
      {launch !== null && (
        <List.Item>
          Рекламодателями домены станут после «Перевести» в блоке «К письму»: новых{' '}
          {formatNumber(launch.fresh)} из {formatNumber(launch.domains)}
        </List.Item>
      )}
      {launch !== null && launch.fresh > 0 && (
        <List.Item>
          Адрес уже есть у {formatNumber(launch.with_address)} — остальным его найдёт поиск после
          перевода
        </List.Item>
      )}
      <List.Item>Письма сами не уйдут: их собирает «Собрать» на экране писем</List.Item>
      {plan.standing.waiting_dr > 0 && (
        <List.Item>
          Ещё {formatNumber(plan.standing.waiting_dr)} «пишем» ждут DR и в полосу не идут: без него
          не видно, крупный ли сайт
        </List.Item>
      )}
    </List>
  );
}

/** План полосы: сколько, что произойдёт, правила и примеры. */
function PlanBody({ plan }: { plan: JudgePlan }) {
  if (plan.pairs === 0) {
    return (
      <Text size="sm" c="dimmed">
        Пар с советом «{BAND_WORDS[plan.band]}» в очереди нет.
      </Text>
    );
  }
  return (
    <Stack gap="sm">
      <Text size="sm" fw={500}>
        {formatNumber(plan.pairs)} {plural(plan.pairs, 'пара', 'пары', 'пар')} у{' '}
        {formatNumber(plan.donors)} {plural(plan.donors, 'донора', 'доноров', 'доноров')}
      </Text>
      {plan.band === 'skip' ? <SkipEffects plan={plan} /> : <WriteEffects plan={plan} />}
      <Stack gap={4}>
        <Text size="sm" fw={500}>
          По правилам судьи
        </Text>
        <Folded
          name="Правила судьи в полосе"
          rows={plan.rules.map((rule) => (
            <List.Item key={rule.rule}>
              {rule.words} — {formatNumber(rule.pairs)}
            </List.Item>
          ))}
        />
      </Stack>
      <Stack gap={4}>
        <Text size="sm" fw={500}>
          Примеры · {formatNumber(plan.examples.length)}
        </Text>
        <Folded
          name="Примеры пар полосы"
          rows={plan.examples.map((pair) => (
            <List.Item
              key={`${pair.donor_host} ${pair.target_root}`}
              style={{ overflowWrap: 'anywhere' }}
            >
              {pair.donor_host} → {pair.target_root} · {counted('балл', pair.points)} — {pair.words}
            </List.Item>
          ))}
        />
      </Stack>
    </Stack>
  );
}

/** План в окне: считается, не посчитался или готов. */
function PlanState({ plan, error }: { plan: JudgePlan | undefined; error: Error | null }) {
  if (error !== null) {
    return (
      <Alert color="red" title="План не посчитался">
        {refusalOf(error)}
      </Alert>
    );
  }
  if (plan === undefined) return <Loader aria-label="Считаем план полосы" size="sm" />;
  return <PlanBody plan={plan} />;
}

const OUTCOMES: Record<Exclude<ApplyRun['phase'], 'running'>, { color: string; title: string }> = {
  done: { color: 'green', title: 'Применено' },
  stopped: { color: 'yellow', title: 'Остановлено' },
  failed: { color: 'red', title: 'Применение оборвалось' },
};

/** Ответившие домены: первые поимённо, остальные числом. */
function answeredWords(run: ApplyRun): string {
  const named = run.answered.slice(0, ANSWERED_SHOWN).join(', ');
  const rest = run.answeredTotal - Math.min(run.answered.length, ANSWERED_SHOWN);
  const tail = rest > 0 ? ` и ещё ${formatNumber(rest)}` : '';
  return `Уже ответили: ${formatNumber(run.answeredTotal)} — переписка в «Диалогах» идёт дальше (${named}${tail})`;
}

/** Итог хода строками: нули не называются, кроме главного числа. */
export function outcomeLines(run: ApplyRun): string[] {
  const lines = [`Решений «${BAND_WORDS[run.band]}» поставлено: ${formatNumber(run.applied)}`];
  if (run.error !== null) lines.push(run.error);
  if (run.left > 0) {
    lines.push(`Осталось: ${formatNumber(run.left)} — «Применить» продолжит ту же партию`);
  }
  if (run.decided > 0) {
    lines.push(`Пропущено — пару уже решил человек: ${formatNumber(run.decided)}`);
  }
  if (run.gone > 0) {
    lines.push(`Пропущено — строку заменил новый обход: ${formatNumber(run.gone)}`);
  }
  if (run.removed > 0 || run.stopped > 0) {
    lines.push(
      `Снято рекламодателей: ${formatNumber(run.removed)}, писем из очереди: ${formatNumber(run.stopped)}`,
    );
  }
  if (run.answeredTotal > 0) lines.push(answeredWords(run));
  if (run.band === 'write' && run.applied > 0) {
    lines.push('Дальше — «Перевести» в блоке «К письму»: письма сами не соберутся');
  }
  return lines;
}

/** Ход и итог применения — у той полосы, которую применяли; у соседней его нет. */
function Outcome({ run, band }: { run: ApplyRun | null; band: JudgeBand }) {
  if (run === null || run.band !== band) return null;
  if (run.phase === 'running') {
    const passed = run.applied + run.decided + run.gone;
    return (
      <Group gap="xs" role="status">
        <Loader size="xs" aria-hidden="true" />
        <Text size="sm">
          Применяем: {formatNumber(passed)} из {formatNumber(run.total)}
        </Text>
      </Group>
    );
  }
  const said = OUTCOMES[run.phase];
  return (
    <Alert color={said.color} title={said.title}>
      {outcomeLines(run).map((line) => (
        <Text key={line} size="sm">
          {line}
        </Text>
      ))}
    </Alert>
  );
}

/** Кнопки окна: пока порции идут — только «Остановить». */
function Actions({
  band,
  plan,
  running,
  onClose,
  onHalt,
}: {
  band: JudgeBand;
  plan: JudgePlan | undefined;
  running: boolean;
  onClose: () => void;
  onHalt: () => void;
}) {
  if (running) {
    return (
      <Group justify="flex-end" gap="sm" mt={ACTIONS_GAP}>
        <Button variant="default" className="press" onClick={onHalt}>
          Остановить
        </Button>
      </Group>
    );
  }
  return (
    <Group justify="flex-end" gap="sm" mt={ACTIONS_GAP}>
      <Button variant="default" className="press" onClick={onClose}>
        Закрыть
      </Button>
      <Button type="submit" className="press" disabled={plan === undefined || plan.pairs === 0}>
        Применить: {BAND_WORDS[band]} · {formatNumber(plan?.pairs ?? 0)}
      </Button>
    </Group>
  );
}

/** Какую полосу применить: числа — из плана, а пока он едет — со строки над очередью. */
function BandSwitch({
  band,
  numbers,
  disabled,
  onPick,
}: {
  band: JudgeBand;
  numbers: JudgeStanding | undefined;
  disabled: boolean;
  onPick: (band: JudgeBand) => void;
}) {
  return (
    <SegmentedControl
      aria-label="Какую полосу применить"
      value={band}
      disabled={disabled}
      onChange={(picked) => onPick(picked as JudgeBand)}
      data={[
        { value: 'skip', label: `Не пишем · ${formatNumber(numbers?.skip ?? 0)}` },
        { value: 'write', label: `Пишем · ${formatNumber(numbers?.write ?? 0)}` },
      ]}
      style={{ alignSelf: 'flex-start' }}
    />
  );
}

/** План выбранной полосы — свой ключ и свежий на каждое открытие: очередь могла смениться.
 *  Ответ про другую полосу (едет новый) планом этой не считается. */
function useBandPlan(band: JudgeBand) {
  const found = useQuery({
    queryKey: [...JUDGE_APPLY_KEY, 'plan', band],
    queryFn: () => fetchJudgePlan(band),
    gcTime: 0,
  });
  const plan = found.data?.band === band ? found.data : undefined;
  return { plan, standing: found.data?.standing, error: found.error };
}

export function JudgeApplyModal({
  standing,
  onClose,
}: {
  /** Числа очереди со строки над ней — подписи полос, пока план не пришёл. */
  standing: JudgeStanding | undefined;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const [band, setBand] = useState<JudgeBand>(() => firstBand(standing));
  const found = useBandPlan(band);
  const { run, start, halt } = useJudgeApplyRun(() =>
    client.invalidateQueries({ queryKey: QUEUE_KEY }),
  );
  const running = run?.phase === 'running';
  const plan = found.plan;
  return (
    <Modal
      opened
      onClose={onClose}
      title="Применить советы судьи"
      size="lg"
      classNames={{ content: 'oneScroll' }}
      closeOnClickOutside={!running}
      closeOnEscape={!running}
      withCloseButton={!running}
    >
      <form
        style={COLUMN}
        onSubmit={(event) => {
          event.preventDefault();
          if (plan !== undefined) void start(band, plan);
        }}
      >
        <Stack gap="sm" px={3}>
          <Text size="sm" c="dimmed">
            {INTRO}
          </Text>
          <BandSwitch
            band={band}
            numbers={found.standing ?? standing}
            disabled={running}
            onPick={setBand}
          />
        </Stack>
        <div className="oneScrollArea scrollSlim">
          <Stack gap="md" p={3}>
            <PlanState plan={plan} error={found.error} />
            <Outcome run={run} band={band} />
            <JudgeBatches busy={running} />
          </Stack>
        </div>
        <Actions band={band} plan={plan} running={running} onClose={onClose} onHalt={halt} />
      </form>
    </Modal>
  );
}
