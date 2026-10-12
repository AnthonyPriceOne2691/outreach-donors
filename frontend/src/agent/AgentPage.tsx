/**
 * Агент переписки: по каким настройкам он пишет собеседнику.
 *
 * Решение Anthony 04.10.2026: агент готовит черновики ответов, человек
 * правит и отправляет. Настройки — у каждого этапа свои: донорам мы
 * покупаем размещение и торгуемся вниз, рекламодателям продаём и держим
 * цену.
 *
 * **Сохранение заводит новую версию, а не правит старую** — как у порогов:
 * черновик агента объясняется той версией, по которой написан. Пока версий
 * у этапа нет, агент на нём не пишет, и экран говорит это словами, а
 * умолчания показывает как отправную точку.
 *
 * **Этапы — из реестра сервера** (`AGENT_STAGES`): имя, пояснение и сторона
 * цены приходят с ними. Своего списка у экрана нет — новый этап встаёт на
 * экран строкой реестра, без правки здесь.
 *
 * **Агент продаж — только с правом «Продажи»** (решение Anthony 10.10.2026, П2):
 * сервер без права этап продаж не отдаёт и правку его настроек отказывает, а экран
 * не показывает его и сам: ответ в кэше мог прийти до того, как право сняли.
 *
 * **Набранное не пропадает молча** (проверка прода 10.10.2026): уход по ссылке и
 * закрытие вкладки с несохранённым — с вопросом, этапы названы (`LeaveGuard`).
 *
 * **Этап продаж — в адресе** (`?stage=sales`, 11.10.2026): так экран открывает пункт
 * «Агент продаж» группы меню «Продажи», и выбор продаж в переключателе пишет его туда же —
 * подсвечен пункт того этапа, что на экране. Прочие этапы выбираются, как раньше.
 */

import { Alert, Badge, Card, Group, Loader, Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import type { ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';

import { fetchAgentSettings } from '../api/agent';
import type { AgentStageView } from '../api/agent';
import { refusalOf } from '../api/client';
import { partOf, stageShown, withPart } from '../api/stages';
import { useSession } from '../auth/AuthProvider';
import { LeaveGuard } from '../components/LeaveGuard';
import { PageHead } from '../components/PageHead';
import { StageSwitch } from '../components/StageSwitch';
import { formatDateTime } from '../format';
import { AgentForm } from './AgentForm';
import { AgentHistory } from './AgentHistory';
import { bodyOf, draftOf, refusalsOf, sameDraft } from './agentDraft';
import type { AgentDraft } from './agentDraft';
import { AGENT_KEY, useAgentSave } from './useAgentSave';

/** Как читается предел цены: покупаем — не дороже, продаём — не дешевле. */
const LIMIT: Record<AgentStageView['price_side'], { price: string; limit: string }> = {
  buy: { price: 'Не дороже, $', limit: 'не дороже' },
  sell: { price: 'Не дешевле, $', limit: 'не дешевле' },
};

type Drafts = Partial<Record<string, AgentDraft>>;

function without(drafts: Drafts, stage: string): Drafts {
  const rest = { ...drafts };
  delete rest[stage];
  return rest;
}

/** Что пропадёт при уходе — этапы, где набранное расходится с действующим, тем же
 *  правилом, что «Вернуть действующие» (`sameDraft`); `null` — пропадать нечему. */
function unsavedOf(drafts: Drafts, stages: AgentStageView[]): string | null {
  const titles = stages
    .filter((view) => {
      const draft = drafts[view.stage];
      return draft !== undefined && !sameDraft(draft, view.current?.settings ?? view.defaults);
    })
    .map((view) => `«${view.title}»`);
  if (titles.length === 0) return null;
  return `Не сохранено набранное в настройках агента: ${titles.join(', ')}. Уйдёте — оно пропадёт.`;
}

/** Действует ли агент на этапе — значком и словами. */
function Standing({ view }: { view: AgentStageView }) {
  if (view.current === null) {
    return (
      <Text size="sm" className="agentStanding">
        Этап не настроен — агент на нём не пишет. Ниже — с чего начать: сохраните, и он начнёт
        готовить черновики.
      </Text>
    );
  }
  const { version, created_by: author, created_at: at, settings } = view.current;
  const who = author === null ? formatDateTime(at) : `${author} · ${formatDateTime(at)}`;
  return (
    <Stack gap="xs">
      <Group gap="xs">
        <Badge variant="light" color={settings.enabled ? 'green' : 'yellow'}>
          {settings.enabled ? 'пишет черновики' : 'выключен'}
        </Badge>
        <Text size="sm" className="agentStanding">
          Действует версия {version} — {who}.
        </Text>
      </Group>
      {/* Автопилот выбран раньше, а теперь его не пускает выключатель или код этапа:
          без этих слов человек видит «автопилот» и думает, что агент шлёт сам. */}
      {settings.mode === 'autopilot' && !view.autopilot_allowed ? (
        <Alert
          color="yellow"
          title="Автопилот выбран, но письма сами не уходят"
          // Заголовок — к чернилам, как значки (glass.css): шестая ступень жёлтого
          // на светлой подложке дала 4,39 : 1 при норме 4,5.
          styles={{
            title: { color: 'color-mix(in oklab, var(--alert-color) 40%, var(--ink) 60%)' },
          }}
        >
          {view.autopilot_refusal ?? 'Автопилот этапу сейчас не разрешён'}
        </Alert>
      ) : null}
    </Stack>
  );
}

interface StageProps {
  view: AgentStageView;
  canEdit: boolean;
  /** Набранное на этапе; нет — экран показывает действующие. */
  edit: AgentDraft | undefined;
  onEdit: (draft: AgentDraft | null) => void;
  /** Сохранён этап — его набранное снимается. Этап приходит из сохранения,
   *  а не из экрана: пока шёл запрос, могли переключиться на другой. */
  onSaved: (stage: string) => void;
}

/** Настройки одного этапа: что действует и поля правки. */
function StageSettings({ view, canEdit, edit, onEdit, onSaved }: StageProps) {
  const save = useAgentSave(onSaved);
  const inUse = view.current?.settings ?? view.defaults;
  const draft = edit ?? draftOf(inUse);
  const body = bodyOf(draft);
  const edited = !sameDraft(draft, inUse);
  // Не настроенный этап сохраняют и без правки: умолчания становятся
  // первой версией, и с неё агент начинает писать.
  const nothingToSave = view.current !== null && !edited;
  return (
    <>
      <Standing view={view} />
      <AgentForm
        draft={draft}
        refusals={canEdit ? refusalsOf(draft) : {}}
        canEdit={canEdit}
        priceLabel={LIMIT[view.price_side].price}
        onEdit={(change) => onEdit({ ...draft, ...change })}
        save={{
          busy: save.isPending,
          disabled: !canEdit || body === null || nothingToSave,
          onSave: () => body !== null && save.mutate({ stage: view.stage, body }),
        }}
        onRevert={edited ? () => onEdit(null) : null}
      />
    </>
  );
}

/** Какой этап на экране: продажи — когда они в адресе; иначе выбранный, а пока не выбран —
 *  первый в реестре. Продажи без адреса не держатся: ушли из «Агента продаж» в «Агент
 *  переписки» — экран тот же, а этап — не продажи. */
function shownStage(
  stages: AgentStageView[],
  picked: string | null,
  sales: boolean,
): AgentStageView | undefined {
  if (sales) return stages.find((one) => one.stage === 'sales');
  const chosen = picked === 'sales' ? null : picked;
  return stages.find((one) => one.stage === chosen) ?? stages.at(0);
}

interface BodyProps {
  error: Error | null;
  view: AgentStageView | undefined;
  /** Ответ пришёл, а этапа продаж в нём нет — открыт «Агент продаж». */
  missing: boolean;
  children: (view: AgentStageView) => ReactNode;
}

/** Под переключателем: настройки этапа — или почему их нет: отказ, этапа нет, ответ едет. */
function StageBody({ error, view, missing, children }: BodyProps) {
  if (missing) {
    return <Text size="sm">Этапа продаж у агента на сервере нет — настраивать нечего.</Text>;
  }
  if (error) {
    return (
      <Alert color="red" title="Настройки агента не загрузились">
        {refusalOf(error)}
      </Alert>
    );
  }
  if (view === undefined) return <Loader size="sm" aria-label="Загружаем настройки агента" />;
  return children(view);
}

/** Выбранный этап: продажи — в адресе (`?stage=sales`), прочие — в состоянии экрана.
 *  Выбор пишет адрес заменой записи: продажи — с частью, прочие — без неё. */
function useStagePick(canSales: boolean) {
  const [params, setParams] = useSearchParams();
  const [picked, setPicked] = useState<string | null>(null);
  const choose = (next: string) => {
    setPicked(next);
    setParams((current) => withPart(current, next === 'sales' ? 'sales' : null), {
      replace: true,
    });
  };
  return { sales: canSales && partOf(params) === 'sales', picked, choose };
}

export function AgentPage() {
  const { can } = useSession();
  const { sales, picked, choose } = useStagePick(can('sales'));
  // Черновик у каждого этапа свой: переключение этапа не теряет набранное.
  const [drafts, setDrafts] = useState<Drafts>({});
  const { data, error } = useQuery({ queryKey: AGENT_KEY, queryFn: fetchAgentSettings });
  // Пока этап не выбран — первый в реестре из тех, что учётка видит.
  const stages = (data?.stages ?? []).filter((one) => stageShown(one.stage, can('sales')));
  const view = shownStage(stages, picked, sales);

  const content = (
    <StageBody error={error} view={view} missing={sales && data !== undefined && !view}>
      {(shown) => (
        <StageSettings
          key={shown.stage}
          view={shown}
          canEdit={can('settings')}
          edit={drafts[shown.stage]}
          onEdit={(draft) =>
            setDrafts((all) =>
              draft === null ? without(all, shown.stage) : { ...all, [shown.stage]: draft },
            )
          }
          onSaved={(saved) => setDrafts((all) => without(all, saved))}
        />
      )}
    </StageBody>
  );

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap="md">
          {/* Заголовок стоит при любой загрузке и любом отказе, как у писем;
              переключатель — с ответом сервера: этапы знает его реестр. */}
          <PageHead
            title={sales ? 'Агент продаж' : 'Агент переписки'}
            hint="Агент готовит черновик ответа собеседнику по этим настройкам, человек правит его и отправляет. Сохранение заводит новую версию: черновик объясняется той, по которой написан."
          />
          {view !== undefined && (
            <StageSwitch
              label="Этап"
              value={view.stage}
              onChange={choose}
              lead={view.lead}
              stages={stages.map((one) => ({ value: one.stage, label: one.title }))}
            />
          )}
          {content}
        </Stack>
      </Card>

      {view !== undefined && <AgentHistory view={view} limitWord={LIMIT[view.price_side].limit} />}
      <LeaveGuard unsaved={unsavedOf(drafts, stages)} />
    </Stack>
  );
}
