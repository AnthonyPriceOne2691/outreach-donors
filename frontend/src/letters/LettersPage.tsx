/**
 * Письма: очередь на отправку с предпросмотром.
 *
 * Единственный экран, где человек смотрит на текст до того, как тот
 * уйдёт постороннему. Отсюда всё его устройство.
 *
 * **Очередь слева, письмо справа.** Список нужен, чтобы выбирать,
 * а решение принимают по тексту — значит текст не прячется за щелчком
 * в отдельное окно. На узком окне столбцы складываются в один.
 *
 * **Отправка — по одному письму.** Кнопки «отправить всё» здесь нет
 * и не будет: она отменяет смысл экрана.
 *
 * **Что мешает отправке, сказано до нажатия, а не после.** Незаполненная
 * обязательная настройка и ненастоящий транспорт — не мелкий шрифт внизу,
 * а предупреждение рядом с кнопкой, и кнопка при первом из них
 * не нажимается.
 *
 * **Этапы не смешиваются.** Вопрос донору о цене и оффер рекламодателю
 * читаются разными глазами и уходят с разных доменов: у каждого своя
 * очередь, свой текст по умолчанию и своя воронка. У рекламодателей нет
 * прогонов — их находит обход доноров, — поэтому и выбора прогонов нет.
 */

import {
  Alert,
  Badge,
  Button,
  Card,
  Grid,
  Group,
  Loader,
  NumberInput,
  SegmentedControl,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';

import { buildLetters, editLetter, listLetters, sendLetter, skipLetter } from '../api/letters';
import { mailSettingsList } from '../api/labels';
import type { LetterDraft, LetterStage, QueuedLetter } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { Metric } from '../components/Metric';
import { LetterDraftEditor, draftOf, sameDraft } from './LetterDraftEditor';
import { LetterPreview, percentOf, toneOf } from './LetterPreview';
import { RunPicker } from './RunPicker';
import { JobLine } from '../jobs/JobLine';

const LETTERS_QUERY_KEY = ['letters'] as const;

function refusalOf(error: unknown): string {
  return error instanceof Error ? error.message : 'Сервер отказал без объяснения';
}

/** Где экран помнит номер последней сборки — у каждого этапа свой. */
const BUILD_JOB_KEY = 'letters:last-build-job';

/** Где экран помнит выбранный этап: вернувшись, человек продолжает там же. */
const STAGE_KEY = 'letters:stage';

function jobKeyOf(stage: LetterStage): string {
  return stage === 'donors' ? BUILD_JOB_KEY : `${BUILD_JOB_KEY}:${stage}`;
}

function remembered(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function remember(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Хранилище недоступно (приватное окно) — помним до перезагрузки.
  }
}

const STAGES: { value: LetterStage; label: string }[] = [
  { value: 'donors', label: 'Донорам' },
  { value: 'advertisers', label: 'Рекламодателям' },
];

/** Что экран говорит об этапе — словами человека. */
const ABOUT: Record<LetterStage, { lead: string; placeholder: string; who: string }> = {
  donors: {
    lead:
      'Очередь на отправку. Приветствие, вступление и вопрос переписаны моделью под ' +
      'конкретного донора; оффер, условия и подпись неизменны — модель их не видит вовсе.',
    placeholder: 'Май, ниша ремонта',
    who: 'донор',
  },
  advertisers: {
    lead:
      'Оффер рекламодателям под найденную ссылку: площадка, страница и анкор стоят в ' +
      'неизменяемой части письма дословно, цена донора не называется. Приветствие, вступление ' +
      'и вопрос переписаны моделью под рекламодателя.',
    placeholder: 'Сентябрь, рекламодатели ставок',
    who: 'рекламодатель',
  },
};

export function LettersPage() {
  const { can } = useSession();
  const queryClient = useQueryClient();
  const [stage, setStage] = useState<LetterStage>(() =>
    remembered(STAGE_KEY) === 'advertisers' ? 'advertisers' : 'donors',
  );
  const [chosen, setChosen] = useState<number | null>(null);
  const [campaign, setCampaign] = useState('');
  const [limit, setLimit] = useState<number>(50);
  // Сроки добивок задаются здесь, при создании рассылки: их подбирают
  // по отклику, и у рассылки, которая уже идёт, они меняться не должны.
  // Пусто — значит взять умолчание сервера.
  const [followups, setFollowups] = useState<(number | null)[]>([null, null]);
  // Правка текста письма. `null` — не трогали: тогда на сервер ничего
  // не уходит, и одноимённая рассылка дополняется своим текстом.
  const [letterEdit, setLetterEdit] = useState<LetterDraft | null>(null);
  // Прогоны рассылки: письма получают только принятые доноры выбранных.
  const [runIds, setRunIds] = useState<number[]>([]);
  // Номер последней сборки переживает перезагрузку страницы: сборка идёт
  // минутами, и человек, вернувшийся к экрану, должен увидеть, чем кончилась.
  const [buildJobs, setBuildJobs] = useState<Record<LetterStage, string | null>>(() => ({
    donors: remembered(jobKeyOf('donors')),
    advertisers: remembered(jobKeyOf('advertisers')),
  }));
  const buildJob = buildJobs[stage];

  const { data, isLoading, error } = useQuery({
    queryKey: [...LETTERS_QUERY_KEY, stage],
    queryFn: () => listLetters(stage),
  });

  // Правка текста, выбранные прогоны и письмо — свои у каждого этапа:
  // текст вопроса донору, уехавший в оффер рекламодателю, сервер
  // не примет, а человек не поймёт, откуда он взялся.
  const switchStage = (next: LetterStage) => {
    setStage(next);
    setChosen(null);
    setLetterEdit(null);
    setRunIds([]);
    remember(STAGE_KEY, next);
  };

  // `data?.letters ?? []` в теле создаёт новый массив на каждую отрисовку,
  // и наблюдатель за очередью срабатывал бы всегда. Пустой список — константа.
  const letters = useMemo(() => data?.letters ?? [], [data]);
  const defaultDays = useMemo(() => data?.followup_default ?? [], [data]);
  const letterDefault = data?.letter_default ?? null;
  const letterChanged =
    letterEdit !== null && letterDefault !== null && !sameDraft(letterEdit, draftOf(letterDefault));
  const selected = letters.find((letter) => letter.id === chosen) ?? letters[0] ?? null;

  // Выбранное письмо могло уйти из очереди — отправили, пропустили.
  // Тогда выбор снимается, иначе экран показывает письмо, которого
  // в очереди уже нет.
  useEffect(() => {
    if (chosen !== null && !letters.some((letter) => letter.id === chosen)) setChosen(null);
  }, [chosen, letters]);

  const refresh = () => queryClient.invalidateQueries({ queryKey: LETTERS_QUERY_KEY });

  const build = useMutation({
    mutationFn: () =>
      buildLetters({
        campaign: campaign.trim(),
        stage,
        limit,
        followup_days: followups.map((days, index) => days ?? defaultDays[index] ?? 0),
        ...(letterChanged && letterEdit !== null ? { letter: letterEdit } : {}),
        // У рекламодателей прогонов нет: сервер откажет, если их прислать.
        ...(stage === 'donors' ? { run_ids: runIds } : {}),
      }),
    onSuccess: async (queued) => {
      setBuildJobs((was) => ({ ...was, [stage]: queued.job_id }));
      remember(jobKeyOf(stage), queued.job_id);
      await refresh();
      notifications.show({
        message: 'Сборка ушла в очередь задач: каждое письмо стоит вызова модели, это минуты',
        color: 'green',
      });
    },
    onError: (failure) =>
      notifications.show({ title: 'Не собрали', message: refusalOf(failure), color: 'red' }),
  });

  const send = useMutation({
    mutationFn: (id: number) => sendLetter(id),
    onSuccess: async (result) => {
      await refresh();
      notifications.show({
        message: result.real
          ? `Письмо ушло с ящика ${result.sender_email}`
          : `Письмо помечено отправленным, но наружу НЕ ушло: транспорт не настроен`,
        color: result.real ? 'green' : 'yellow',
      });
    },
    onError: (failure) =>
      notifications.show({ title: 'Не отправили', message: refusalOf(failure), color: 'red' }),
  });

  const skip = useMutation({
    mutationFn: (id: number) => skipLetter(id),
    onSuccess: async () => {
      await refresh();
      notifications.show({
        message: `Письмо убрано из очереди, этот ${ABOUT[stage].who} в следующей сборке не появится`,
        color: 'yellow',
      });
    },
    onError: (failure) =>
      notifications.show({ title: 'Не убрали', message: refusalOf(failure), color: 'red' }),
  });

  const save = useMutation({
    mutationFn: ({ id, subject, body }: { id: number; subject: string; body: string }) =>
      editLetter(id, { subject, body }),
    onSuccess: async (letter) => {
      await refresh();
      notifications.show({
        message: `Сохранено, отличие от шаблона — ${percentOf(letter.uniqueness)}`,
        color: letter.verdict === null ? 'green' : 'yellow',
      });
    },
    onError: (failure) =>
      notifications.show({ title: 'Не сохранили', message: refusalOf(failure), color: 'red' }),
  });

  if (isLoading) return <Loader aria-label="Загружаем очередь писем" m="md" />;
  if (error) {
    return (
      <Alert color="red" title="Очередь не загрузилась" m="md">
        {refusalOf(error)}
      </Alert>
    );
  }

  const view = data!;
  const offCorridor = letters.filter((letter) => letter.verdict !== null).length;
  const busy = send.isPending || skip.isPending || save.isPending;

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap="md">
          <Stack gap={6}>
            <Title order={3}>Письма</Title>
            <SegmentedControl
              aria-label="Кому письма"
              value={stage}
              onChange={(value) => switchStage(value as LetterStage)}
              data={STAGES}
              style={{ alignSelf: 'flex-start' }}
            />
            <Text size="sm" c="dimmed" maw={680}>
              {ABOUT[stage].lead}
            </Text>
          </Stack>

          <Grid gutter="sm">
            <Grid.Col span={{ base: 6, sm: 3 }}>
              <Metric title="В очереди" value={letters.length} />
            </Grid.Col>
            <Grid.Col span={{ base: 6, sm: 3 }}>
              <Metric
                title="Вне коридора"
                value={offCorridor}
                hint={`коридор ${Math.round(view.corridor.min * 100)}–${Math.round(view.corridor.max * 100)}%`}
                color={offCorridor > 0 ? 'yellow' : undefined}
              />
            </Grid.Col>
            <Grid.Col span={{ base: 6, sm: 3 }}>
              <Metric
                title="Ещё не писали"
                value={view.funnel['ещё не писали'] ?? 0}
                hint={
                  stage === 'donors'
                    ? `подходящих ${view.funnel['подходящих'] ?? 0}`
                    : `рекламодателей ${view.funnel['рекламодателей'] ?? 0}`
                }
              />
            </Grid.Col>
            <Grid.Col span={{ base: 6, sm: 3 }}>
              <Metric
                title="Почта"
                value={view.transport.real ? view.transport.name : 'не подключена'}
                hint={view.transport.real ? 'письма уходят' : 'подключается на рабочем сервере'}
                color={view.transport.real ? undefined : 'yellow'}
              />
            </Grid.Col>
          </Grid>

          {view.blocked_by.length > 0 ? (
            <Alert color="yellow" title="Отправка пока не подключена">
              Не задано: {mailSettingsList(view.blocked_by)} — настраивается при подключении почты.
              Очередь собирается и видна, письма можно читать и править; отправить их получится
              после подключения.
            </Alert>
          ) : null}

          {view.transport.problem !== null ? (
            <Alert color="yellow" title="Почта не подключилась">
              {view.transport.problem}
            </Alert>
          ) : null}

          {can('send') ? (
            <Group align="flex-end" gap="sm">
              <TextInput
                label="Кампания"
                description="Одноимённая дополняется, а не заводится второй раз"
                placeholder={ABOUT[stage].placeholder}
                value={campaign}
                w={280}
                onChange={(event) => setCampaign(event.currentTarget.value)}
              />
              <NumberInput
                label="Писем за раз"
                description="Каждое стоит вызова модели"
                value={limit}
                min={1}
                max={500}
                w={180}
                onChange={(value) => setLimit(typeof value === 'number' ? value : 50)}
              />
              {defaultDays.map((fallback, index) => (
                <NumberInput
                  key={index}
                  label={`Добивка ${index + 1}, дней`}
                  description={index === 0 ? 'после первого письма' : 'после предыдущей'}
                  value={followups[index] ?? fallback}
                  min={0}
                  max={90}
                  w={150}
                  onChange={(value) =>
                    setFollowups((was) =>
                      was.map((old, at) =>
                        at === index ? (typeof value === 'number' ? value : null) : old,
                      ),
                    )
                  }
                />
              ))}
              <Button
                color="lagoon"
                className="press"
                loading={build.isPending}
                disabled={campaign.trim() === '' || (stage === 'donors' && runIds.length === 0)}
                onClick={() => build.mutate()}
              >
                Собрать очередь
              </Button>
            </Group>
          ) : null}

          {buildJob !== null ? (
            <JobLine jobId={buildJob} onFinished={() => void refresh()} />
          ) : null}

          {can('send') && stage === 'donors' ? (
            <RunPicker value={runIds} onChange={setRunIds} />
          ) : null}

          {can('send') && letterDefault !== null ? (
            <LetterDraftEditor
              key={stage}
              stage={stage}
              fallback={letterDefault}
              value={letterEdit ?? draftOf(letterDefault)}
              onChange={setLetterEdit}
            />
          ) : null}
        </Stack>
      </Card>

      {letters.length === 0 ? (
        <Card className="glass" p="xl">
          <Stack gap="xs">
            <Text fw={500}>Очередь пуста</Text>
            <Text size="sm" c="dimmed">
              {stage === 'donors' ? (
                <>
                  Подходящих доноров {view.funnel['подходящих'] ?? 0}, из них с адресом{' '}
                  {view.funnel['с адресом'] ?? 0}, и ещё не писали{' '}
                  {view.funnel['ещё не писали'] ?? 0}. Если последнее число ноль — написаны все;
                  если ноль второе — пора добрать контакты.
                </>
              ) : (
                <>
                  Рекламодателей {view.funnel['рекламодателей'] ?? 0}, из них с найденной ссылкой{' '}
                  {view.funnel['со ссылкой'] ?? 0}, со свежей ценой донора{' '}
                  {view.funnel['цена донора свежая'] ?? 0}, с адресом{' '}
                  {view.funnel['с адресом'] ?? 0}, и ещё не писали{' '}
                  {view.funnel['ещё не писали'] ?? 0}. Ноль на цене значит, что сначала нужны ответы
                  доноров с ценой: оффер «мы дешевле» без неё не пишется; ноль на адресе — пора
                  искать контакты рекламодателей.
                </>
              )}
            </Text>
          </Stack>
        </Card>
      ) : (
        <Grid gutter="lg" align="flex-start">
          <Grid.Col span={{ base: 12, md: 5 }}>
            <Card className="glass" p="xs">
              <Stack gap={4}>
                {letters.map((letter) => (
                  <LetterRow
                    key={letter.id}
                    letter={letter}
                    active={selected?.id === letter.id}
                    onChoose={() => setChosen(letter.id)}
                  />
                ))}
              </Stack>
            </Card>
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 7 }}>
            {selected !== null ? (
              <LetterPreview
                letter={selected}
                corridor={view.corridor}
                blockedBy={view.blocked_by}
                transportIsReal={view.transport.real}
                canSend={can('send')}
                busy={busy}
                onSend={() => send.mutate(selected.id)}
                onSkip={() => skip.mutate(selected.id)}
                onSave={(subject, body) => save.mutate({ id: selected.id, subject, body })}
              />
            ) : null}
          </Grid.Col>
        </Grid>
      )}
    </Stack>
  );
}

interface RowProps {
  letter: QueuedLetter;
  active: boolean;
  onChoose: () => void;
}

/** Строка очереди. Щёлкают по ней целиком — значит её и поднимаем
 *  под курсором: внутри нет ни одной кнопки, в которую можно промахнуться. */
function LetterRow({ letter, active, onChoose }: RowProps) {
  return (
    <Card
      className={active ? 'glassQuiet press' : 'glassSlot press liftable'}
      p="sm"
      style={{ cursor: 'pointer' }}
      onClick={onChoose}
      aria-current={active ? 'true' : undefined}
    >
      <Group justify="space-between" wrap="nowrap" gap="sm">
        <Stack gap={2} style={{ minWidth: 0 }}>
          <Text fw={active ? 600 : 500} truncate>
            {letter.host}
          </Text>
          <Text size="xs" c="dimmed" truncate>
            {letter.email ?? 'адрес не определён'}
          </Text>
        </Stack>
        <Badge variant="light" color={toneOf(letter)}>
          {percentOf(letter.uniqueness)}
        </Badge>
      </Group>
    </Card>
  );
}
