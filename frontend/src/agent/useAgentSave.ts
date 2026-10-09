/**
 * Сохранение настроек агента: новая версия этапа и словами — что вышло.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';

import { saveAgentSettings } from '../api/agent';
import type { AgentSettingsBody } from '../api/agent';
import { refusalOf } from '../api/client';
import { notify } from '../notices';

export const AGENT_KEY = ['agent-settings'] as const;

/** После сохранения черновик этапа снимается (`onSaved`): экран показывает
 *  сохранённое, а не набранное. */
export function useAgentSave(onSaved: (stage: string) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ stage, body }: { stage: string; body: AgentSettingsBody }) =>
      saveAgentSettings(stage, body),
    onSuccess: async (version, { stage }) => {
      onSaved(stage);
      await queryClient.invalidateQueries({ queryKey: AGENT_KEY });
      notify({
        color: 'green',
        message: `Настройки агента сохранены как версия ${version.version}.`,
      });
    },
    onError: (failure) =>
      notify({
        color: 'red',
        title: 'Настройки агента не сохранены',
        message: refusalOf(failure),
      }),
  });
}
