/**
 * Les deux listes que laisse un précomptage, montées pour de vrai.
 *
 * Elles portaient des issues à trancher, et elles n'en portent plus : un
 * journal de précomptage est posté dans l'ERP *avant* que la photo du jour J ne
 * soit prise, donc la photo l'a déjà intégré. Trancher revenait à compter deux
 * fois la même correction.
 *
 * Ce que ce fichier tient, c'est que la suppression est **complète et dite** :
 * plus aucun bouton d'issue, plus aucune colonne d'issue, et un bandeau qui
 * explique qu'il n'y a rien à faire. Retirer les boutons sans le dire laisserait
 * un écran qui montre deux colonnes discordantes et se lit comme une tâche en
 * attente — ce qui est pire que le bouton.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { DriftsTab, LabelsTab } from './earlyCounts.observations'
import { ToastProvider } from '../components/ui'

vi.mock('../lib/api', () => ({
  api: {
    drifts: () =>
      Promise.resolve([
        {
          id: 'd-1',
          campaignId: 'camp-1',
          erpJournalId: 'j-1',
          warehouseId: 'ATP',
          locationId: 'SOL',
          itemNumber: 'MEL-STA-4412',
          qtyCountedT0: 40,
          qtyErpJ: 37,
          driftQty: -3,
          driftValue: -45.6,
        },
      ]),
    labelAlerts: () =>
      Promise.resolve([
        {
          labelId: '001609233',
          itemNumber: 'MEL-STA-4412',
          sealedWarehouseId: 'ATP',
          sealedLocationId: 'SOL',
          otherWarehouseId: 'ATP',
          otherLocationId: 'QUAI EXP',
          otherJournalNumber: 'NPEM-523004',
          otherQtyCounted: 8,
        },
      ]),
    recountedInPlace: () =>
      Promise.resolve([
        {
          sealedWarehouseId: 'ATP',
          sealedLocationId: 'SF1',
          ownerJournalNumber: 'NPEM-521215',
          otherJournalNumber: 'NPEM-522821',
          labelCount: 6,
        },
      ]),
  },
}))

function show(tab: 'derives' | 'etiquettes') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        {tab === 'derives' ? (
          <DriftsTab campaignId="camp-1" />
        ) : (
          <LabelsTab campaignId="camp-1" />
        )}
      </ToastProvider>
    </QueryClientProvider>,
  )
}

describe('les dérives', () => {
  it('montrent les deux termes de la soustraction et son résultat', async () => {
    show('derives')
    // Compté au précomptage, ERP du jour J, et la dérive. Deux termes, parce
    // qu'il n'y en a plus trois : `ERP@T0` a disparu avec la référence qu'il
    // portait.
    expect(await screen.findByText('MEL-STA-4412')).toBeTruthy()
    expect(screen.getByText('Compté au précomptage')).toBeTruthy()
    expect(screen.getByText('ERP jour J')).toBeTruthy()
    expect(screen.queryByText(/ERP avant précomptage/)).toBeNull()
  })

  it('n’offrent aucune issue, et disent pourquoi', async () => {
    show('derives')
    await screen.findByText('MEL-STA-4412')
    expect(screen.getByText('À regarder, pas à trancher')).toBeTruthy()
    for (const gone of [
      'Conserver le comptage avancé',
      'Recompter le jour J',
      'À trancher',
    ]) {
      expect(screen.queryByText(gone)).toBeNull()
    }
  })

  it('n’ont plus de colonne « Issue »', async () => {
    // La colonne restait vide sur toutes les lignes : une colonne qui ne peut
    // plus prendre de valeur se lit comme une saisie qu'on a oubliée de faire.
    show('derives')
    await screen.findByText('MEL-STA-4412')
    expect(screen.queryByText('Issue')).toBeNull()
  })
})

describe('les étiquettes', () => {
  it('montrent où la pièce a reparu', async () => {
    show('etiquettes')
    expect(await screen.findByText('001609233')).toBeTruthy()
    expect(screen.getByText('ATP / QUAI EXP')).toBeTruthy()
    expect(screen.getByText('NPEM-523004')).toBeTruthy()
  })

  it('n’offrent plus les trois issues', async () => {
    show('etiquettes')
    await screen.findByText('001609233')
    for (const gone of [
      'La mettre au nouvel emplacement',
      'L’enlever du nouvel emplacement',
      'Signaler : à rescanner',
    ]) {
      expect(screen.queryByRole('button', { name: gone })).toBeNull()
    }
    expect(screen.getByText('À regarder, pas à trancher')).toBeTruthy()
  })

  it('gardent le bandeau des emplacements recomptés sur place', async () => {
    // Ces lignes-là sortent de la liste — la pièce n'a pas bougé — et les
    // retirer en silence cacherait un fait : deux journaux ont compté le même
    // emplacement, et un seul est retenu.
    show('etiquettes')
    expect(
      await screen.findByText(/Emplacements recomptés sur place/),
    ).toBeTruthy()
    expect(screen.getByText(/retenu NPEM-521215, ignoré NPEM-522821/)).toBeTruthy()
  })
})
