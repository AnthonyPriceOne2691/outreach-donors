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
 *
 * **Смена этапа не перерисовывает экран.** Заголовок и переключатель стоят,
 * прежняя очередь видна приглушённой, пока не придёт новая, и не нажимается:
 * письмо одного этапа под переключателем другого — не то, что отправляют.
 * Раньше экран целиком сменялся значком загрузки и рисовался заново — «как
 * будто страница загружается заново» (замечание 25.09.2026).
 *
 * **Выбранное письмо всегда на виду.** На широком окне предпросмотр
 * закреплён рядом с очередью: письмо из середины списка открывалось в
 * четырёх с половиной тысячах пикселей выше окна. На узком предпросмотр
 * стоит над очередью, и выбор письма прокручивает к нему — а не оставляет
 * его после семидесяти семи строк списка.
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
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useMediaQuery, useReducedMotion } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { Dispatch, RefObject, SetStateAction } from 'react';

import { refusalOf } from '../api/client';
import { buildLetters, editLetter, listLetters, sendLetter, skipLetter } from '../api/letters';
import { mailSettingsList, settingsInWords } from '../api/labels';
import type { Corridor, LetterDraft, LettersView, QueuedLetter } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { HintLabel } from '../components/HintLabel';
import { StageSwitch } from '../components/StageSwitch';
import { formatNumber, formatPercent } from '../format';
import { LetterDraftEditor, draftOf, sameDraft } from './LetterDraftEditor';
import { LetterPreview, toneOf } from './LetterPreview';
import { mailTile } from './mailTile';
import { uniquenessText } from './letterText';
import { RunPicker } from './RunPicker';
import { QueueHead } from './QueueHead';
import { SendQueue } from './SendQueue';
import type { LetterTarget } from './targets';
import { ABOUT, TARGETS, audienceOf, emptyQueueText, poolHint, stageOf, targetOf } from './targets';
import { UnknownOutcome } from './UnknownOutcome';
import { remember, remembered } from '../storage';
import { JobLine } from '../jobs/JobLine';

/** Уже этого очередь и письмо стоят друг под другом (граница `md` у сетки). */
const ONE_COLUMN = '(max-width: 61.99em)';

/** Прокрутка к письму: плавно, а у того, кто просил без движения, — сразу. */
function scrollTo(calm: boolean): ScrollIntoViewOptions {
  return { behavior: calm ? 'auto' : 'smooth', block: 'start' };
}

const LETTERS_QUERY_KEY = ['letters'] as const;

/** Где экран помнит номер последней сборки — у каждого этапа свой. */
const BUILD_JOB_KEY = 'letters:last-build-job';

/** Где экран помнит выбранный этап: вернувшись, человек продолжает там же. */
const STAGE_KEY = 'letters:stage';

function jobKeyOf(target: LetterTarget): string {
  return target === 'donors' ? BUILD_JOB_KEY : `${BUILD_JOB_KEY}:${target}`;
}

export function LettersPage() {
  const { can } = useSession();
  const queryClient = useQueryClient();
  const [target, setTarget] = useState<LetterTarget>(() => targetOf(remembered(STAGE_KEY)));
  const stage = stageOf(target);
  const [chosen, setChosen] = useState<number | null>(null);
  // После «Отправить» следующее письмо само не открывается (боевой прогон
  // 06.10): на месте кнопки оказывалось письмо другому донору, и второй
  // щелчок ушёл бы ему без подтверждения. Следующее открывает человек.
  const [held, setHeld] = useState(false);
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
  const [buildJobs, setBuildJobs] = useState<Record<LetterTarget, string | null>>(() => ({
    donors: remembered(jobKeyOf('donors')),
    advertisers: remembered(jobKeyOf('advertisers')),
    niche: remembered(jobKeyOf('niche')),
  }));
  const buildJob = buildJobs[target];
  const oneColumn = useMediaQuery(ONE_COLUMN) === true;
  const calm = useReducedMotion();
  const previewRef = useRef<HTMLDivElement>(null);
  // Выбор письма на узком окне прокручивает к нему — один раз, после того
  // как выбранное письмо отрисовано, а не на каждую перерисовку.
  const reveal = useRef(false);

  const query = useQuery({
    queryKey: [...LETTERS_QUERY_KEY, target],
    queryFn: () => listLetters(stage, audienceOf(target)),
    // Пока идёт очередь другого этапа, стоит прежняя — приглушённой. Без
    // этого экран целиком менялся на значок загрузки и рисовался заново.
    placeholderData: keepPreviousData,
  });
  const { data, error } = query;
  const stale = query.isPlaceholderData;

  // Правка текста, выбранные прогоны и письмо — свои у каждого этапа:
  // текст вопроса донору, уехавший в оффер рекламодателю, сервер
  // не примет, а человек не поймёт, откуда он взялся.
  const switchStage = (next: LetterTarget) => {
    setTarget(next);
    setChosen(null);
    setHeld(false);
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
  const selected = held
    ? null
    : (letters.find((letter) => letter.id === chosen) ?? letters[0] ?? null);

  // Выбранное письмо могло уйти из очереди — отправили, пропустили.
  // Тогда выбор снимается, иначе экран показывает письмо, которого
  // в очереди уже нет.
  useEffect(() => {
    if (chosen !== null && !letters.some((letter) => letter.id === chosen)) setChosen(null);
  }, [chosen, letters]);

  useEffect(() => {
    if (!reveal.current) return;
    reveal.current = false;
    previewRef.current?.scrollIntoView(scrollTo(calm));
  }, [selected?.id, calm]);

  const choose = (id: number) => {
    // Рядом с очередью письмо и так на виду; в одну колонку оно над
    // списком, и без прокрутки выбор выглядел бы как «ничего не произошло».
    if (oneColumn && id === selected?.id) {
      previewRef.current?.scrollIntoView(scrollTo(calm));
      return;
    }
    reveal.current = oneColumn;
    setHeld(false);
    setChosen(id);
  };

  const refresh = () => queryClient.invalidateQueries({ queryKey: LETTERS_QUERY_KEY });

  const build = useMutation({
    mutationFn: () =>
      buildLetters({
        campaign: campaign.trim(),
        stage,
        audience: audienceOf(target),
        limit,
        followup_days: followups.map((days, index) => days ?? defaultDays[index] ?? 0),
        ...(letterChanged && letterEdit !== null ? { letter: letterEdit } : {}),
        // У рекламодателей прогонов нет: сервер откажет, если их прислать.
        ...(stage === 'donors' ? { run_ids: runIds } : {}),
      }),
    onSuccess: async (queued) => {
      setBuildJobs((was) => ({ ...was, [target]: queued.job_id }));
      remember(jobKeyOf(target), queued.job_id);
      await refresh();
      notifications.show({
        message: 'Сборка ушла в очередь задач: каждое письмо стоит вызова модели, это минуты',
        color: 'green',
      });
    },
    onError: (failure) =>
      notifications.show({
        title: 'Не собрали',
        message: settingsInWords(refusalOf(failure)),
        color: 'red',
      }),
  });

  const send = useMutation({
    mutationFn: (id: number) => sendLetter(id),
    onSuccess: async (result) => {
      setHeld(true);
      await refresh();
      notifications.show({
        message: result.real
          ? `Письмо ушло с ящика ${result.sender_email}`
          : `Письмо помечено отправленным, но наружу НЕ ушло: транспорт не настроен`,
        color: result.real ? 'green' : 'yellow',
      });
    },
    onError: (failure) =>
      notifications.show({
        title: 'Не отправили',
        message: settingsInWords(refusalOf(failure)),
        color: 'red',
      }),
  });

  const skip = useMutation({
    mutationFn: (id: number) => skipLetter(id),
    onSuccess: async () => {
      await refresh();
      notifications.show({
        message: `Письмо убрано из очереди, этот ${ABOUT[target].who} в следующей сборке не появится`,
        color: 'yellow',
      });
    },
    onError: (failure) =>
      notifications.show({
        title: 'Не убрали',
        message: settingsInWords(refusalOf(failure)),
        color: 'red',
      }),
  });

  const save = useMutation({
    mutationFn: ({ id, subject, body }: { id: number; subject: string; body: string }) =>
      editLetter(id, { subject, body }),
    onSuccess: async (letter) => {
      await refresh();
      notifications.show({
        message: `Сохранено, отличие от шаблона — ${
          data === undefined
            ? formatPercent(letter.uniqueness)
            : uniquenessText(letter.uniqueness, data.corridor)
        }`,
        color: letter.verdict === null ? 'green' : 'yellow',
      });
    },
    onError: (failure) =>
      notifications.show({
        title: 'Не сохранили',
        message: settingsInWords(refusalOf(failure)),
        color: 'red',
      }),
  });

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap="md">
          {/* Заголовок и переключатель стоят при любой загрузке и любом
              отказе: без переключателя с этапа, который не загрузился,
              было бы не уйти. */}
          <Stack gap={6}>
            <Title order={3}>Письма</Title>
            <StageSwitch
              label="Кому письма"
              value={target}
              onChange={switchStage}
              stages={TARGETS}
              lead={ABOUT[target].lead}
            />
          </Stack>

          {data === undefined && error === null ? (
            <Loader size="sm" aria-label="Загружаем очередь писем" />
          ) : null}
          {data === undefined && error !== null ? (
            <Alert color="red" title="Очередь не загрузилась">
              {refusalOf(error)}
            </Alert>
          ) : null}

          {data !== undefined ? (
            <QueueControls
              view={data}
              target={target}
              stale={stale}
              canSend={can('send')}
              campaign={campaign}
              onCampaign={setCampaign}
              limit={limit}
              onLimit={setLimit}
              followups={followups}
              onFollowups={setFollowups}
              building={build.isPending}
              buildBlocked={
                campaign.trim() === ''
                  ? 'Назовите кампанию'
                  : stage === 'donors' && runIds.length === 0
                    ? 'Отметьте прогоны рассылки'
                    : null
              }
              onBuild={() => build.mutate()}
              buildJob={buildJob}
              onBuildFinished={() => void refresh()}
              runIds={runIds}
              onRunIds={setRunIds}
              letterEdit={letterEdit}
              onLetterEdit={setLetterEdit}
            />
          ) : null}
        </Stack>
      </Card>

      {/* Зависшие письма — над пачкой: их исход решают до того, как слать дальше. У каждой
          вкладки свои: письмо бизнеса ниши решают там, откуда ушла его пачка. */}
      <UnknownOutcome stage={stage} audience={audienceOf(target)} canSend={can('send')} />

      {/* Пачка — вкладки: на «Бизнесам ниши» уходят только их письма, на «Рекламодателям» —
          только письма по найденной ссылке; число на кнопке — очередь этой вкладки. */}
      {data !== undefined ? (
        <QueueHead
          total={data.queued_total}
          letters={letters}
          corridor={data.corridor}
          send={
            can('send') && !stale ? (
              <SendQueue
                stage={stage}
                audience={audienceOf(target)}
                count={data.queued_total}
                batchMax={data.batch_max}
                blocked={data.blocked_by.length > 0}
                onFinished={() => void refresh()}
              />
            ) : null
          }
        />
      ) : null}

      {data !== undefined ? (
        <Queue
          view={data}
          target={target}
          stale={stale}
          letters={letters}
          selected={selected}
          previewRef={previewRef}
          canSend={can('send')}
          busy={send.isPending || skip.isPending || save.isPending}
          onChoose={choose}
          onSend={(id) => send.mutate(id)}
          onSkip={(id) => skip.mutate(id)}
          onSave={(id, subject, body) => save.mutate({ id, subject, body })}
        />
      ) : null}
    </Stack>
  );
}

interface ControlsProps {
  view: LettersView;
  target: LetterTarget;
  /** Показана очередь прежнего этапа, пока идёт новая. */
  stale: boolean;
  canSend: boolean;
  campaign: string;
  onCampaign: (value: string) => void;
  limit: number;
  onLimit: (value: number) => void;
  followups: (number | null)[];
  onFollowups: Dispatch<SetStateAction<(number | null)[]>>;
  building: boolean;
  /** Чего не хватает для сборки — словами у кнопки; `null` — собрать можно. */
  buildBlocked: string | null;
  onBuild: () => void;
  buildJob: string | null;
  onBuildFinished: () => void;
  runIds: number[];
  onRunIds: (next: number[]) => void;
  letterEdit: LetterDraft | null;
  onLetterEdit: (next: LetterDraft) => void;
}

/** Сводка этапа, препятствия отправке и сборка очереди. */
function QueueControls({
  view,
  target,
  stale,
  canSend,
  campaign,
  onCampaign,
  limit,
  onLimit,
  followups,
  onFollowups,
  building,
  buildBlocked,
  onBuild,
  buildJob,
  onBuildFinished,
  runIds,
  onRunIds,
  letterEdit,
  onLetterEdit,
}: ControlsProps) {
  const mail = mailTile(view.transport);
  const defaultDays = view.followup_default;
  const letterDefault = view.letter_default;

  return (
    // Прежний этап — приглушён и не нажимается: текст первого письма
    // донорам, поправленный под переключателем «Рекламодателям», сервер
    // бы не принял, а человек не понял бы, откуда он взялся.
    <Stack gap="md" className="staleRows" data-stale={stale || undefined} inert={stale}>
      {/* Факты этапа — строкой, а не четырьмя плитками по ~105 px под «0 / 0 / 0 /
          SendGrid» (аудит экранов 09.10.2026): «В очереди» и «Вне коридора» — в шапке
          самой очереди (`QueueHead`), где по ним действуют; почта — значком, янтарём,
          только когда письма не уходят. */}
      <Group gap="md" align="center">
        <Group gap={6} align="center">
          <Text size="sm">Почта</Text>
          <Badge variant="light" color={view.transport.real ? 'green' : 'yellow'}>
            {mail.value}
          </Badge>
          <Text size="sm" c="dimmed">
            {mail.hint}
          </Text>
        </Group>
        <Text size="sm">
          Ещё не писали <b>{formatNumber(view.funnel['ещё не писали'] ?? 0)}</b> ·{' '}
          {poolHint(view.funnel, target)}
        </Text>
      </Group>

      {view.blocked_by.length > 0 ? (
        <Alert color="yellow" title="Отправка пока не подключена">
          Не задано: {mailSettingsList(view.blocked_by)} — настраивается при подключении почты.
          Очередь собирается и видна, письма можно читать и править; отправить их получится после
          подключения.
        </Alert>
      ) : null}

      {view.transport.problem !== null ? (
        // Текст отказа транспорта пишется для журнала и называет переменные
        // окружения; на экране — словами (`settingsInWords`).
        <Alert color="yellow" title="Почта не подключилась">
          {settingsInWords(view.transport.problem)}
        </Alert>
      ) : null}

      {/* Порядок формы — порядок решения: кампания и числа, прогоны, текст первого
          письма, и кнопка — последней (аудит экранов 09.10.2026: «Собрать очередь»
          стояла посреди формы, обязательные прогоны и текст — под ней, а выключенная
          кнопка не говорила, чего не хватает). Поля — по значению, пояснения — в «i». */}
      {canSend ? (
        <Stack gap="sm">
          <Group align="flex-end" gap="sm">
            <TextInput
              labelProps={{ labelElement: 'div' }}
              label={
                <HintLabel
                  label="Кампания"
                  hint="Одноимённая дополняется, а не заводится второй раз."
                />
              }
              aria-label="Кампания"
              placeholder={ABOUT[target].placeholder}
              value={campaign}
              w={280}
              onChange={(event) => onCampaign(event.currentTarget.value)}
            />
            <NumberInput
              labelProps={{ labelElement: 'div' }}
              label={
                <HintLabel
                  label="За раз"
                  hint="Писем за одну сборку. Каждое стоит вызова модели."
                />
              }
              aria-label="Писем за раз"
              value={limit}
              min={1}
              max={500}
              w="6.5rem"
              onChange={(value) => onLimit(typeof value === 'number' ? value : 50)}
            />
            {defaultDays.map((fallback, index) => (
              <NumberInput
                key={index}
                labelProps={{ labelElement: 'div' }}
                label={
                  <HintLabel
                    label={`Добивка ${index + 1}`}
                    hint={
                      index === 0
                        ? 'Через сколько дней после первого письма.'
                        : 'Через сколько дней после предыдущей добивки.'
                    }
                  />
                }
                aria-label={`Добивка ${index + 1}, дней`}
                suffix=" дн."
                value={followups[index] ?? fallback}
                min={0}
                max={90}
                w="7rem"
                onChange={(value) =>
                  onFollowups((was) =>
                    was.map((old, at) =>
                      at === index ? (typeof value === 'number' ? value : null) : old,
                    ),
                  )
                }
              />
            ))}
          </Group>

          {target === 'donors' ? <RunPicker value={runIds} onChange={onRunIds} /> : null}

          <LetterDraftEditor
            key={target}
            target={target}
            fallback={letterDefault}
            value={letterEdit ?? draftOf(letterDefault)}
            onChange={onLetterEdit}
          />

          <Group gap="sm">
            <Button
              color="lagoon"
              className="press"
              loading={building}
              disabled={buildBlocked !== null}
              onClick={onBuild}
            >
              Собрать очередь
            </Button>
            {buildBlocked !== null && (
              <Text size="sm" c="dimmed">
                {buildBlocked}
              </Text>
            )}
          </Group>
        </Stack>
      ) : null}

      {buildJob !== null ? <JobLine jobId={buildJob} onFinished={onBuildFinished} /> : null}
    </Stack>
  );
}

interface QueueProps {
  view: LettersView;
  target: LetterTarget;
  stale: boolean;
  letters: QueuedLetter[];
  selected: QueuedLetter | null;
  previewRef: RefObject<HTMLDivElement | null>;
  canSend: boolean;
  busy: boolean;
  onChoose: (id: number) => void;
  onSend: (id: number) => void;
  onSkip: (id: number) => void;
  onSave: (id: number, subject: string, body: string) => void;
}

/** Очередь и выбранное письмо — или объяснение, почему очередь пуста. */
function Queue({
  view,
  target,
  stale,
  letters,
  selected,
  previewRef,
  canSend,
  busy,
  onChoose,
  onSend,
  onSkip,
  onSave,
}: QueueProps) {
  if (letters.length === 0) {
    return (
      <Card className="glass staleRows" p="xl" data-stale={stale || undefined}>
        <Stack gap="xs">
          <Text fw={500}>Очередь пуста</Text>
          <Text size="sm" c="dimmed">
            {emptyQueueText(view.funnel, target)}
          </Text>
        </Stack>
      </Card>
    );
  }

  return (
    <Grid
      gutter="lg"
      align="flex-start"
      className="staleRows"
      data-stale={stale || undefined}
      inert={stale}
      aria-busy={stale || undefined}
    >
      {/* На узком окне письмо — над очередью: иначе оно стояло после всех
          строк списка, и выбранное приходилось искать прокруткой. */}
      <Grid.Col span={{ base: 12, md: 5 }} order={{ base: 2, md: 1 }}>
        <Card className="glass" p="xs">
          <Stack gap={4}>
            {letters.map((letter) => (
              <LetterRow
                key={letter.id}
                letter={letter}
                corridor={view.corridor}
                active={selected?.id === letter.id}
                onChoose={() => onChoose(letter.id)}
              />
            ))}
          </Stack>
        </Card>
      </Grid.Col>
      <Grid.Col
        span={{ base: 12, md: 7 }}
        order={{ base: 1, md: 2 }}
        className="letterPreviewCol"
        ref={previewRef}
      >
        {selected === null ? (
          <Card className="glass" p="xl">
            <Stack gap="xs" align="flex-start">
              <Text fw={500}>Письмо ушло</Text>
              <Text size="sm" c="dimmed">
                Следующее не открывается само: на месте кнопки «Отправить» оказалось бы письмо
                другому адресату. Выберите его в очереди.
              </Text>
              <Button
                variant="light"
                className="press"
                onClick={() => letters[0] && onChoose(letters[0].id)}
              >
                Открыть следующее
              </Button>
            </Stack>
          </Card>
        ) : (
          <LetterPreview
            letter={selected}
            corridor={view.corridor}
            blockedBy={view.blocked_by}
            transport={view.transport}
            canSend={canSend}
            busy={busy}
            onSend={() => onSend(selected.id)}
            onSkip={() => onSkip(selected.id)}
            onSave={(subject, body) => onSave(selected.id, subject, body)}
          />
        )}
      </Grid.Col>
    </Grid>
  );
}

interface RowProps {
  letter: QueuedLetter;
  corridor: Corridor;
  active: boolean;
  onChoose: () => void;
}

/** Строка очереди. Щёлкают по ней целиком — и с клавиатуры тоже: до этого
 *  выбрать письмо без мыши было нельзя вовсе. */
function LetterRow({ letter, corridor, active, onChoose }: RowProps) {
  return (
    <Card
      className={active ? 'glassQuiet press' : 'glassSlot press liftable'}
      p="sm"
      role="button"
      tabIndex={0}
      style={{ cursor: 'pointer' }}
      onClick={onChoose}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onChoose();
        }
      }}
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
          {uniquenessText(letter.uniqueness, corridor)}
        </Badge>
      </Group>
    </Card>
  );
}
